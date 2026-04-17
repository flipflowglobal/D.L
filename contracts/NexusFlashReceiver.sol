// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright (c) 2026 Darcel King. All rights reserved.
// Proprietary — see LICENSE in repository root.
pragma solidity ^0.8.20;

/**
 * @title  NexusFlashReceiver
 * @notice Production-grade Aave V3 flash loan receiver for multi-DEX arbitrage.
 *         Executes atomic swap sequences across Uniswap V3, Curve Finance,
 *         Balancer V2, and Camelot V3.
 *
 * Architecture:
 *   1. Python bot detects arbitrage opportunity off-chain (Bellman-Ford graph)
 *   2. Bot encodes trade route and calls executeArbitrage()
 *   3. Contract borrows asset via Aave V3 flashLoanSimple
 *   4. Aave calls executeOperation() with borrowed funds
 *   5. Contract executes DEX swaps in sequence
 *   6. Contract repays loan + premium, keeps profit
 *   7. Profit sent to owner wallet
 *
 * Supported DEXes:
 *   0 = Uniswap V3
 *   1 = Curve Finance (via Router)
 *   2 = Balancer V2
 *   3 = Camelot V3 (Arbitrum-native fork of Uniswap V3)
 */

import "./interfaces/IAaveV3Pool.sol";
import "./interfaces/IFlashLoanSimpleReceiver.sol";
import "./interfaces/IUniswapV3Router.sol";
import "./interfaces/ICurvePool.sol";
import "./interfaces/IBalancerVault.sol";

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function allowance(address owner, address spender) external view returns (uint256);
}

contract NexusFlashReceiver is IFlashLoanSimpleReceiver {

    // ─── DEX Type Constants ───────────────────────────────────────────────────
    uint8 constant DEX_UNISWAP_V3  = 0;
    uint8 constant DEX_CURVE       = 1;
    uint8 constant DEX_BALANCER    = 2;
    uint8 constant DEX_CAMELOT_V3  = 3;

    // ─── Immutable Protocol Addresses ─────────────────────────────────────────
    IAaveV3Pool          public immutable aavePool;
    IUniswapV3SwapRouter public immutable uniswapRouter;
    ICurveRouter         public immutable curveRouter;
    IBalancerVault       public immutable balancerVault;

    // ─── Mutable State ────────────────────────────────────────────────────────
    address public owner;
    address public executor;          // Python bot hot wallet
    bool    public paused;
    uint256 public totalProfit;       // Lifetime accumulated profit (token-denominated)

    // ─── Events ───────────────────────────────────────────────────────────────
    event ArbitrageExecuted(
        address indexed asset,
        uint256 borrowed,
        uint256 profit,
        uint256 gasUsed
    );
    event Paused(bool state);
    event ExecutorUpdated(address newExecutor);
    event EmergencyWithdraw(address token, uint256 amount);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    // ─── Custom Errors ────────────────────────────────────────────────────────
    error Unauthorized();
    error ContractPaused();
    error InvalidCaller();
    error InsufficientProfit(uint256 repayAmount, uint256 balance);
    error SwapFailed(uint8 dexType, uint256 step);
    error ZeroAmount();

    // ─── Swap Step Struct ─────────────────────────────────────────────────────
    /**
     * @param dexType       DEX identifier (0=UniV3, 1=Curve, 2=Balancer, 3=Camelot)
     * @param tokenIn       Input token address
     * @param tokenOut      Output token address
     * @param amountIn      Exact input (0 = use full contract balance of tokenIn)
     * @param minAmountOut  Minimum acceptable output (slippage protection)
     * @param extraData     ABI-encoded DEX-specific parameters
     */
    struct SwapStep {
        uint8   dexType;
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint256 minAmountOut;
        bytes   extraData;
    }

    // ─── Constructor ──────────────────────────────────────────────────────────
    constructor(
        address _aavePool,
        address _uniswapRouter,
        address _curveRouter,
        address _balancerVault
    ) {
        owner         = msg.sender;
        executor      = msg.sender;
        aavePool      = IAaveV3Pool(_aavePool);
        uniswapRouter = IUniswapV3SwapRouter(_uniswapRouter);
        curveRouter   = ICurveRouter(_curveRouter);
        balancerVault = IBalancerVault(_balancerVault);
    }

    // ─── Access Control Modifiers ─────────────────────────────────────────────
    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    modifier onlyExecutor() {
        if (msg.sender != executor && msg.sender != owner) revert Unauthorized();
        _;
    }

    modifier notPaused() {
        if (paused) revert ContractPaused();
        _;
    }

    // ─── Entry Point ──────────────────────────────────────────────────────────
    /**
     * @notice Initiates a flash loan arbitrage trade.
     * @param asset     Token to borrow (e.g. WETH, USDC)
     * @param amount    Amount to borrow in token's native decimals
     * @param steps     Ordered array of swap steps to execute atomically
     */
    function executeArbitrage(
        address asset,
        uint256 amount,
        SwapStep[] calldata steps
    ) external onlyExecutor notPaused {
        if (amount == 0) revert ZeroAmount();
        bytes memory params = abi.encode(steps);
        aavePool.flashLoanSimple(
            address(this),
            asset,
            amount,
            params,
            0   // referral code
        );
    }

    // ─── Aave V3 Flash Loan Callback ──────────────────────────────────────────
    /**
     * @notice Called by Aave after flash loan is disbursed.
     *         Executes all swap steps and repays loan + premium.
     */
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        if (msg.sender != address(aavePool)) revert InvalidCaller();
        if (initiator != address(this))      revert InvalidCaller();

        uint256 gasStart = gasleft();

        SwapStep[] memory steps = abi.decode(params, (SwapStep[]));

        for (uint256 i = 0; i < steps.length; i++) {
            _executeSwap(steps[i]);
        }

        uint256 repayAmount = amount + premium;
        uint256 balance     = IERC20(asset).balanceOf(address(this));

        if (balance < repayAmount) {
            revert InsufficientProfit(repayAmount, balance);
        }

        uint256 profit = balance - repayAmount;

        _safeApprove(asset, address(aavePool), repayAmount);

        if (profit > 0) {
            IERC20(asset).transfer(owner, profit);
            totalProfit += profit;
        }

        emit ArbitrageExecuted(asset, amount, profit, gasStart - gasleft());
        return true;
    }

    // ─── Internal Swap Dispatcher ─────────────────────────────────────────────
    function _executeSwap(SwapStep memory step) internal {
        uint256 amountIn = step.amountIn == 0
            ? IERC20(step.tokenIn).balanceOf(address(this))
            : step.amountIn;

        if (step.dexType == DEX_UNISWAP_V3 || step.dexType == DEX_CAMELOT_V3) {
            _swapUniswapV3(step, amountIn);
        } else if (step.dexType == DEX_CURVE) {
            _swapCurve(step, amountIn);
        } else if (step.dexType == DEX_BALANCER) {
            _swapBalancer(step, amountIn);
        } else {
            revert("NexusFlashReceiver: unsupported dexType");
        }
    }

    // ─── Uniswap V3 / Camelot V3 ──────────────────────────────────────────────
    function _swapUniswapV3(SwapStep memory step, uint256 amountIn) internal {
        (uint24 fee, address routerAddr) = abi.decode(step.extraData, (uint24, address));
        address router = routerAddr != address(0) ? routerAddr : address(uniswapRouter);
        _safeApprove(step.tokenIn, router, amountIn);
        IUniswapV3SwapRouter(router).exactInputSingle(
            IUniswapV3SwapRouter.ExactInputSingleParams({
                tokenIn:           step.tokenIn,
                tokenOut:          step.tokenOut,
                fee:               fee,
                recipient:         address(this),
                amountIn:          amountIn,
                amountOutMinimum:  step.minAmountOut,
                sqrtPriceLimitX96: 0
            })
        );
    }

    // ─── Curve Finance ────────────────────────────────────────────────────────
    function _swapCurve(SwapStep memory step, uint256 amountIn) internal {
        (
            address[11] memory route,
            uint256[5][5] memory swapParams,
            address[5] memory pools
        ) = abi.decode(step.extraData, (address[11], uint256[5][5], address[5]));
        _safeApprove(step.tokenIn, address(curveRouter), amountIn);
        curveRouter.exchange(route, swapParams, amountIn, step.minAmountOut, pools);
    }

    // ─── Balancer V2 ──────────────────────────────────────────────────────────
    function _swapBalancer(SwapStep memory step, uint256 amountIn) internal {
        bytes32 poolId = abi.decode(step.extraData, (bytes32));
        _safeApprove(step.tokenIn, address(balancerVault), amountIn);
        balancerVault.swap(
            IBalancerVault.SingleSwap({
                poolId:   poolId,
                kind:     IBalancerVault.SwapKind.GIVEN_IN,
                assetIn:  step.tokenIn,
                assetOut: step.tokenOut,
                amount:   amountIn,
                userData: ""
            }),
            IBalancerVault.FundManagement({
                sender:              address(this),
                fromInternalBalance: false,
                recipient:           payable(address(this)),
                toInternalBalance:   false
            }),
            step.minAmountOut,
            block.timestamp
        );
    }

    // ─── Admin Functions ──────────────────────────────────────────────────────
    function setExecutor(address _executor) external onlyOwner {
        executor = _executor;
        emit ExecutorUpdated(_executor);
    }

    function setPaused(bool _paused) external onlyOwner {
        paused = _paused;
        emit Paused(_paused);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "NexusFlashReceiver: new owner is zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    /// @notice Emergency: sweep any ERC-20 token stuck in contract.
    function emergencyWithdraw(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        if (balance > 0) {
            IERC20(token).transfer(owner, balance);
            emit EmergencyWithdraw(token, balance);
        }
    }

    /// @notice Emergency: sweep native ETH mistakenly sent to contract.
    function emergencyWithdrawETH() external onlyOwner {
        uint256 balance = address(this).balance;
        if (balance > 0) {
            (bool success, ) = payable(owner).call{value: balance}("");
            require(success, "NexusFlashReceiver: ETH transfer failed");
        }
    }

    // ─── Internal Helpers ────────────────────────────────────────────────────

    /// @dev Call an ERC-20 function and require success + optional bool return.
    ///      Handles both tokens that revert on failure and non-standard tokens
    ///      that return false instead of reverting (e.g. older USDT).
    function _callOptionalReturn(address token, bytes memory data) internal {
        (bool success, bytes memory returnData) = token.call(data);
        require(success, "NexusFlashReceiver: ERC20 call failed");
        if (returnData.length > 0) {
            require(abi.decode(returnData, (bool)), "NexusFlashReceiver: ERC20 operation failed");
        }
    }

    function _safeApprove(address token, address spender, uint256 amount) internal {
        // Reset to 0 first (required by some tokens, e.g. USDT)
        _callOptionalReturn(token, abi.encodeWithSelector(IERC20.approve.selector, spender, 0));
        _callOptionalReturn(token, abi.encodeWithSelector(IERC20.approve.selector, spender, amount));
    }

    receive() external payable {}
}
