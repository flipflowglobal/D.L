// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ── Aave V3 ───────────────────────────────────────────────────────────────────

interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

interface IAavePool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

// ── ERC-20 ────────────────────────────────────────────────────────────────────

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

// ── Uniswap V3 ────────────────────────────────────────────────────────────────

interface IUniswapV3Router {
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

// ── Uniswap / SushiSwap V2-style ──────────────────────────────────────────────

interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

// ── Curve (stable / crypto pools) ────────────────────────────────────────────

interface ICurvePool {
    // Stable-swap variant  (int128 indices)
    function exchange(int128 i, int128 j, uint256 dx, uint256 min_dy) external returns (uint256);
    // Crypto-swap variant  (uint256 indices)
    function exchange(uint256 i, uint256 j, uint256 dx, uint256 min_dy) external returns (uint256);
}

// ── Balancer V2 ───────────────────────────────────────────────────────────────

interface IBalancerVault {
    enum SwapKind { GIVEN_IN, GIVEN_OUT }
    struct SingleSwap {
        bytes32 poolId;
        SwapKind kind;
        address assetIn;
        address assetOut;
        uint256 amount;
        bytes userData;
    }
    struct FundManagement {
        address sender;
        bool fromInternalBalance;
        address payable recipient;
        bool toInternalBalance;
    }
    function swap(
        SingleSwap calldata singleSwap,
        FundManagement calldata funds,
        uint256 limit,
        uint256 deadline
    ) external payable returns (uint256 amountCalculated);
}

// ═════════════════════════════════════════════════════════════════════════════
//  NexusFlashReceiver
//
//  Multi-DEX flash-loan arbitrage receiver for Aave V3.
//  Supports up to 8 sequential swap steps per flash loan, mixing:
//    • Uniswap V3  (DEX_UNI_V3)
//    • SushiSwap V2  (DEX_SUSHI_V2)
//    • Curve stable/crypto  (DEX_CURVE)
//    • Balancer V2  (DEX_BALANCER)
//    • Camelot V2 (Arbitrum V2-fork)  (DEX_CAMELOT)
//
//  One-call flow (triggered by initiate()):
//    1. Owner calls initiate(asset, amount, steps, minProfit)
//    2. Aave lends `amount` of `asset` to this contract
//    3. executeOperation() runs all swap steps in sequence
//    4. Net profit check: finalBalance >= amount + premium + minProfit
//    5. Approve Aave repayment; profit stays in contract
//    6. Owner calls withdraw() to collect profit
//
//  Security:
//    - onlyOwner and onlyPool guards on all state-changing functions
//    - Profit floor enforced before repayment approval
//    - No external calls are made outside of well-known DEX interfaces
// ═════════════════════════════════════════════════════════════════════════════

contract NexusFlashReceiver is IFlashLoanSimpleReceiver {

    // ── DEX type identifiers ──────────────────────────────────────────────────
    uint8 public constant DEX_UNI_V3    = 0;
    uint8 public constant DEX_SUSHI_V2  = 1;
    uint8 public constant DEX_CURVE     = 2;
    uint8 public constant DEX_BALANCER  = 3;
    uint8 public constant DEX_CAMELOT   = 4;

    // ── State ─────────────────────────────────────────────────────────────────
    address public immutable owner;
    address public immutable aavePool;

    // ── Events ────────────────────────────────────────────────────────────────
    event FlashExecuted(address indexed asset, uint256 borrowed, uint256 profit, uint256 steps);
    event ProfitWithdrawn(address indexed token, address indexed to, uint256 amount);

    // ── Modifiers ─────────────────────────────────────────────────────────────
    modifier onlyOwner() {
        require(msg.sender == owner, "NFR: not owner");
        _;
    }

    modifier onlyPool() {
        require(msg.sender == aavePool, "NFR: not pool");
        _;
    }

    // ── Constructor ───────────────────────────────────────────────────────────
    constructor(address _aavePool) {
        require(_aavePool != address(0), "NFR: zero pool");
        owner    = msg.sender;
        aavePool = _aavePool;
    }

    // ── SwapStep encoding ─────────────────────────────────────────────────────
    //
    // Each SwapStep is ABI-encoded as:
    //   (uint8 dex, address router, address tokenIn, address tokenOut,
    //    uint256 amountOutMin, uint24 fee, bytes32 balancerPoolId,
    //    int128 curveI, int128 curveJ, uint256 deadline)
    //
    // Unused fields should be zero-filled.
    //
    struct SwapStep {
        uint8   dex;             // DEX_* constant
        address router;          // router / vault / pool address
        address tokenIn;
        address tokenOut;
        uint256 amountOutMin;    // minimum received (slippage guard)
        uint24  fee;             // Uniswap V3 fee tier (ignored for other DEXes)
        bytes32 balancerPoolId;  // Balancer pool id (ignored for non-Balancer)
        int128  curveI;          // Curve token-in index
        int128  curveJ;          // Curve token-out index
        uint256 deadline;        // Unix timestamp
    }

    // ── Initiate flash loan ───────────────────────────────────────────────────

    /**
     * @notice Trigger a multi-DEX flash-loan arbitrage.  Only the owner may call.
     * @param asset      ERC-20 token to borrow (must be Aave-listed)
     * @param amount     Amount to borrow (token's native decimals)
     * @param steps      Encoded SwapStep[] (abi.encode(SwapStep[]))
     * @param minProfit  Minimum net profit required (same decimals as asset)
     */
    function initiate(
        address asset,
        uint256 amount,
        bytes calldata steps,
        uint256 minProfit
    ) external onlyOwner {
        // Pack minProfit into params so executeOperation can access it
        bytes memory params = abi.encode(steps, minProfit);
        IAavePool(aavePool).flashLoanSimple(
            address(this),
            asset,
            amount,
            params,
            0   // referral code
        );
    }

    // ── Aave callback ─────────────────────────────────────────────────────────

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override onlyPool returns (bool) {
        require(initiator == address(this), "NFR: invalid initiator");

        (bytes memory stepsEncoded, uint256 minProfit) =
            abi.decode(params, (bytes, uint256));

        SwapStep[] memory steps = abi.decode(stepsEncoded, (SwapStep[]));
        require(steps.length > 0, "NFR: empty steps");
        require(steps.length <= 8, "NFR: too many steps");

        // Track the contract's pre-existing balance separately so profitability
        // is measured only on the flash-loan operation, in the borrowed asset.
        uint256 startingAssetBalance = IERC20(asset).balanceOf(address(this)) - amount;

        // Execute each swap step sequentially.
        // The first step receives `amount` of `asset` as input.
        uint256 currentAmount = amount;

        for (uint256 i = 0; i < steps.length; i++) {
            currentAmount = _executeStep(steps[i], currentAmount);
        }

        // ── Profit check ──────────────────────────────────────────────────────
        uint256 finalAssetBalance = IERC20(asset).balanceOf(address(this));
        uint256 amountReturned = finalAssetBalance - startingAssetBalance;
        uint256 repay = amount + premium;
        require(amountReturned >= repay, "NFR: arb not profitable");
        uint256 profit = amountReturned - repay;
        require(profit >= minProfit, "NFR: profit below floor");

        // ── Repay Aave ────────────────────────────────────────────────────────
        IERC20(asset).approve(aavePool, repay);
        // Aave pulls repay from this contract; remaining profit stays here.

        emit FlashExecuted(asset, amount, profit, steps.length);
        return true;
    }

    // ── Internal: dispatch swap by DEX type ───────────────────────────────────

    function _executeStep(SwapStep memory s, uint256 amountIn)
        internal returns (uint256 amountOut)
    {
        if (s.dex == DEX_UNI_V3) {
            return _swapUniV3(s, amountIn);
        } else if (s.dex == DEX_SUSHI_V2 || s.dex == DEX_CAMELOT) {
            return _swapV2Style(s, amountIn);
        } else if (s.dex == DEX_CURVE) {
            return _swapCurve(s, amountIn);
        } else if (s.dex == DEX_BALANCER) {
            return _swapBalancer(s, amountIn);
        }
        revert("NFR: unknown DEX");
    }

    function _swapUniV3(SwapStep memory s, uint256 amountIn)
        internal returns (uint256 amountOut)
    {
        IERC20(s.tokenIn).approve(s.router, amountIn);
        IUniswapV3Router.ExactInputSingleParams memory p =
            IUniswapV3Router.ExactInputSingleParams({
                tokenIn:           s.tokenIn,
                tokenOut:          s.tokenOut,
                fee:               s.fee,
                recipient:         address(this),
                deadline:          s.deadline,
                amountIn:          amountIn,
                amountOutMinimum:  s.amountOutMin,
                sqrtPriceLimitX96: 0
            });
        amountOut = IUniswapV3Router(s.router).exactInputSingle(p);
    }

    function _swapV2Style(SwapStep memory s, uint256 amountIn)
        internal returns (uint256 amountOut)
    {
        IERC20(s.tokenIn).approve(s.router, amountIn);
        address[] memory path = new address[](2);
        path[0] = s.tokenIn;
        path[1] = s.tokenOut;
        uint256[] memory amounts = IUniswapV2Router(s.router)
            .swapExactTokensForTokens(
                amountIn, s.amountOutMin, path, address(this), s.deadline
            );
        amountOut = amounts[amounts.length - 1];
    }

    function _callCurveExchange(SwapStep memory s, uint256 amountIn)
        internal returns (uint256 amountOut)
    {
        (bool ok, bytes memory data) = s.router.call(
            abi.encodeWithSignature(
                "exchange(int128,int128,uint256,uint256)",
                s.curveI,
                s.curveJ,
                amountIn,
                s.amountOutMin
            )
        );
        if (ok) {
            return abi.decode(data, (uint256));
        }

        require(s.curveI >= 0 && s.curveJ >= 0, "NFR: negative curve index");

        (ok, data) = s.router.call(
            abi.encodeWithSignature(
                "exchange(uint256,uint256,uint256,uint256)",
                uint256(uint128(s.curveI)),
                uint256(uint128(s.curveJ)),
                amountIn,
                s.amountOutMin
            )
        );
        require(ok, "NFR: curve exchange failed");
        return abi.decode(data, (uint256));
    }

    function _swapCurve(SwapStep memory s, uint256 amountIn)
        internal returns (uint256 amountOut)
    {
        IERC20(s.tokenIn).approve(s.router, amountIn);
        amountOut = _callCurveExchange(s, amountIn);
    }

    function _swapBalancer(SwapStep memory s, uint256 amountIn)
        internal returns (uint256 amountOut)
    {
        IERC20(s.tokenIn).approve(s.router, amountIn);
        IBalancerVault.SingleSwap memory swap = IBalancerVault.SingleSwap({
            poolId:   s.balancerPoolId,
            kind:     IBalancerVault.SwapKind.GIVEN_IN,
            assetIn:  s.tokenIn,
            assetOut: s.tokenOut,
            amount:   amountIn,
            userData: ""
        });
        IBalancerVault.FundManagement memory funds = IBalancerVault.FundManagement({
            sender:              address(this),
            fromInternalBalance: false,
            recipient:           payable(address(this)),
            toInternalBalance:   false
        });
        amountOut = IBalancerVault(s.router).swap(swap, funds, s.amountOutMin, s.deadline);
    }

    // ── Admin ─────────────────────────────────────────────────────────────────

    /// Withdraw any ERC-20 token (profit or stuck tokens) to owner.
    function withdraw(address token) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        require(bal > 0, "NFR: nothing to withdraw");
        IERC20(token).transfer(owner, bal);
        emit ProfitWithdrawn(token, owner, bal);
    }

    /// Sweep native ETH sent accidentally.
    function withdrawEth() external onlyOwner {
        uint256 bal = address(this).balance;
        require(bal > 0, "NFR: no ETH");
        payable(owner).transfer(bal);
    }

    // ── Fallback ──────────────────────────────────────────────────────────────
    receive() external payable {}
}
