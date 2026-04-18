// SPDX-License-Identifier: AGPL-3.0
pragma solidity ^0.8.20;

interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}
