// SPDX-License-Identifier: LicenseRef-DarcelKing-Proprietary
// Copyright (c) 2026 Darcel King. All Rights Reserved.
pragma solidity ^0.8.20;

// ── Aave V3 Interfaces ────────────────────────────────────────────────────────

interface IFlashLoanSimpleReceiver {
    /**
     * @notice Executes an operation after receiving the flash-loaned assets.
     * @param asset The address of the flash-borrowed asset
     * @param amount The amount of the flash-borrowed asset
     * @param premium The fee of the flash-borrowed asset
     * @param initiator The address that initiated the flash loan
     * @param params Variadic packed params passed to the receiver from the Pool
     * @return True if the execution of the operation succeeds
     */
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

interface IPool {
    /**
     * @notice Allows smartcontracts to access the liquidity of the pool within one
     *         transaction, as long as the amount taken plus a fee is returned.
     * @param receiverAddress The address of the contract receiving the funds
     * @param asset The address of the asset being flash-borrowed
     * @param amount The amount of the asset being flash-borrowed
     * @param params Variadic packed params to pass to the receiver as extra information
     * @param referralCode The code used to register the integrator originating the operation
     */
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IERC20 {
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

// ── Uniswap V3 Interface ──────────────────────────────────────────────────────

interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24  fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut);
}

// ── SushiSwap V2 Interface ────────────────────────────────────────────────────

interface IUniswapV2Router02 {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

// ── ReentrancyGuard ───────────────────────────────────────────────────────────

abstract contract ReentrancyGuard {
    uint256 private constant _NOT_ENTERED = 1;
    uint256 private constant _ENTERED = 2;
    uint256 private _status;

    constructor() {
        _status = _NOT_ENTERED;
    }

    modifier nonReentrant() {
        require(_status != _ENTERED, "ReentrancyGuard: reentrant call");
        _status = _ENTERED;
        _;
        _status = _NOT_ENTERED;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
//  FlashLoanArbitrage
//
//  Executes a flash-loan-funded arbitrage:
//    1. Borrow WETH from Aave V3 via flashLoanSimple
//    2. Sell WETH → USDC on the cheaper DEX (buy side)
//    3. Swap USDC → WETH on the other DEX (sell side)
//    4. Repay Aave (amount + premium)
//    5. Send net profit to owner
//
//  Two execution paths are supported (set via `params` encoding):
//    A. BUY_UNI_SELL_SUSHI: Buy on Uniswap V3, sell on SushiSwap V2
//    B. BUY_SUSHI_SELL_UNI: Buy on SushiSwap V2, sell on Uniswap V3
//
//  Safety:
//    - onlyOwner and onlyPool guards
//    - nonReentrant on executeOperation to prevent reentrancy via token hooks
//    - minProfit check before completing the arb
//    - DRY_RUN flag kept server-side; this contract is never deployed unless
//      DRY_RUN=false and the operator explicitly calls initiate()
// ─────────────────────────────────────────────────────────────────────────────

contract FlashLoanArbitrage is IFlashLoanSimpleReceiver, ReentrancyGuard {

    // ── Constants ─────────────────────────────────────────────────────────────

    // Mainnet
    address public constant AAVE_POOL    = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address public constant WETH         = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address public constant USDC         = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address public constant UNI_ROUTER   = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    address public constant SUSHI_ROUTER = 0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F;

    uint24 public constant UNI_FEE_TIER = 3000; // 0.30 % — highest liquidity

    // Direction flags (encoded in params)
    uint8 public constant BUY_UNI_SELL_SUSHI = 0;
    uint8 public constant BUY_SUSHI_SELL_UNI = 1;

    // ── State ─────────────────────────────────────────────────────────────────

    address public immutable owner;
    address public immutable pool;
    uint256 public minProfitWei;   // minimum acceptable profit in WETH wei

    // ── Events ────────────────────────────────────────────────────────────────

    event ArbitrageExecuted(
        address indexed token,
        uint256 borrowed,
        uint256 repaid,
        uint256 profit,
        uint8   direction
    );

    event ProfitWithdrawn(address indexed to, uint256 amount, address token);

    event MinProfitUpdated(uint256 oldValue, uint256 newValue);

    // ── Modifiers ─────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "FLA: not owner");
        _;
    }

    modifier onlyPool() {
        require(msg.sender == pool, "FLA: not pool");
        _;
    }

    // ── Constructor ───────────────────────────────────────────────────────────

    constructor(address _pool, uint256 _minProfitWei) {
        owner        = msg.sender;
        pool         = _pool;
        minProfitWei = _minProfitWei;
    }

    // ── Flash loan receiver ───────────────────────────────────────────────────

    /**
     * @notice Called by the Aave pool after funds are transferred to this contract.
     *
     * params layout (abi.encode):
     *   (uint8 direction, uint256 amountOutMin, uint256 deadline)
     *
     * direction 0 = BUY_UNI_SELL_SUSHI
     * direction 1 = BUY_SUSHI_SELL_UNI
     */
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override onlyPool nonReentrant returns (bool) {
        require(initiator == address(this), "FLA: invalid initiator");
        require(asset == WETH, "FLA: asset must be WETH");

        (uint8 direction, uint256 amountOutMin, uint256 deadline) =
            abi.decode(params, (uint8, uint256, uint256));

        uint256 repayAmount = amount + premium;

        // ── Execute arbitrage ────────────────────────────────────────────────

        uint256 wethEnd;

        if (direction == BUY_UNI_SELL_SUSHI) {
            // Step 1: WETH → USDC on Uniswap V3
            uint256 usdcReceived = _swapWethToUsdcUniswap(amount, amountOutMin, deadline);
            // Step 2: USDC → WETH on SushiSwap V2 (enforce repayAmount as minimum)
            wethEnd = _swapUsdcToWethSushi(usdcReceived, repayAmount, deadline);
        } else {
            // Step 1: WETH → USDC on SushiSwap V2
            uint256 usdcReceived = _swapWethToUsdcSushi(amount, amountOutMin, deadline);
            // Step 2: USDC → WETH on Uniswap V3 (enforce repayAmount as minimum)
            wethEnd = _swapUsdcToWethUniswap(usdcReceived, repayAmount, deadline);
        }

        // ── Profit check ─────────────────────────────────────────────────────

        require(wethEnd >= repayAmount, "FLA: arb not profitable");
        uint256 profit = wethEnd - repayAmount;
        require(profit >= minProfitWei, "FLA: profit below minimum");

        // ── Repay Aave ────────────────────────────────────────────────────────

        IERC20(WETH).approve(pool, repayAmount);
        // Aave pulls repayAmount from this contract; remaining profit stays here

        emit ArbitrageExecuted(asset, amount, repayAmount, profit, direction);
        return true;
    }

    // ── Initiate flash loan ───────────────────────────────────────────────────

    /**
     * @notice Trigger a flash loan arbitrage.  Only the owner may call this.
     * @param amount        WETH to borrow (18 decimals)
     * @param direction     0 = buy_uni_sell_sushi, 1 = buy_sushi_sell_uni
     * @param amountOutMin  Minimum USDC expected from the first swap (slippage guard)
     * @param deadline      Unix timestamp after which the swap reverts
     */
    function initiate(
        uint256 amount,
        uint8   direction,
        uint256 amountOutMin,
        uint256 deadline
    ) external onlyOwner {
        require(direction <= 1, "FLA: invalid direction");
        bytes memory params = abi.encode(direction, amountOutMin, deadline);
        IPool(pool).flashLoanSimple(
            address(this),
            WETH,
            amount,
            params,
            0  // referral code
        );
    }

    // ── Admin ─────────────────────────────────────────────────────────────────

    function setMinProfit(uint256 _minProfitWei) external onlyOwner {
        uint256 oldValue = minProfitWei;
        minProfitWei = _minProfitWei;
        emit MinProfitUpdated(oldValue, _minProfitWei);
    }

    /// Withdraw any token (WETH profit or stuck tokens) to owner.
    function withdraw(address token) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        require(bal > 0, "FLA: nothing to withdraw");
        IERC20(token).transfer(owner, bal);
        emit ProfitWithdrawn(owner, bal, token);
    }

    /// Convenience: withdraw ETH if any was mistakenly sent.
    function withdrawEth() external onlyOwner {
        uint256 bal = address(this).balance;
        require(bal > 0, "FLA: no ETH");
        (bool success, ) = payable(owner).call{value: bal}("");
        require(success, "FLA: ETH transfer failed");
    }

    // ── Internal swap helpers ─────────────────────────────────────────────────

    function _swapWethToUsdcUniswap(
        uint256 amountIn,
        uint256 amountOutMin,
        uint256 deadline
    ) internal returns (uint256 amountOut) {
        IERC20(WETH).approve(UNI_ROUTER, amountIn);
        ISwapRouter.ExactInputSingleParams memory p = ISwapRouter.ExactInputSingleParams({
            tokenIn:           WETH,
            tokenOut:          USDC,
            fee:               UNI_FEE_TIER,
            recipient:         address(this),
            deadline:          deadline,
            amountIn:          amountIn,
            amountOutMinimum:  amountOutMin,
            sqrtPriceLimitX96: 0
        });
        amountOut = ISwapRouter(UNI_ROUTER).exactInputSingle(p);
    }

    function _swapUsdcToWethUniswap(
        uint256 amountIn,
        uint256 amountOutMin,
        uint256 deadline
    ) internal returns (uint256 amountOut) {
        IERC20(USDC).approve(UNI_ROUTER, amountIn);
        ISwapRouter.ExactInputSingleParams memory p = ISwapRouter.ExactInputSingleParams({
            tokenIn:           USDC,
            tokenOut:          WETH,
            fee:               UNI_FEE_TIER,
            recipient:         address(this),
            deadline:          deadline,
            amountIn:          amountIn,
            amountOutMinimum:  amountOutMin,
            sqrtPriceLimitX96: 0
        });
        amountOut = ISwapRouter(UNI_ROUTER).exactInputSingle(p);
    }

    function _swapWethToUsdcSushi(
        uint256 amountIn,
        uint256 amountOutMin,
        uint256 deadline
    ) internal returns (uint256 amountOut) {
        IERC20(WETH).approve(SUSHI_ROUTER, amountIn);
        address[] memory path = new address[](2);
        path[0] = WETH;
        path[1] = USDC;
        uint256[] memory amounts = IUniswapV2Router02(SUSHI_ROUTER)
            .swapExactTokensForTokens(amountIn, amountOutMin, path, address(this), deadline);
        amountOut = amounts[amounts.length - 1];
    }

    function _swapUsdcToWethSushi(
        uint256 amountIn,
        uint256 amountOutMin,
        uint256 deadline
    ) internal returns (uint256 amountOut) {
        IERC20(USDC).approve(SUSHI_ROUTER, amountIn);
        address[] memory path = new address[](2);
        path[0] = USDC;
        path[1] = WETH;
        uint256[] memory amounts = IUniswapV2Router02(SUSHI_ROUTER)
            .swapExactTokensForTokens(amountIn, amountOutMin, path, address(this), deadline);
        amountOut = amounts[amounts.length - 1];
    }

    // ── Fallback ──────────────────────────────────────────────────────────────

    receive() external payable {}
}
