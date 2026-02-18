# Next Gen Trader

## Goals
1. Review the market's price action, levels, and behaviors with LLMs.
2. Monitor a portfolio full of different strategies and risk assets.
3. Use AI to execute live trades.

## Ideal Workflow
1. Start the IBKR TWS or IBKR API Gateway
2. Boot up the Worker, pull current positions, store them in the DB
3. Boot up the API + UI, view the current portfolio
4. Compare current positions and risk levels to strategies + groupings
5. Execute trades to get to desired levels

## Brokers
I'm planning to use this for my actual trading operations, so:
- [ ] Interactive Brokers (primary)
- [ ] Alpaca (secondary)

## Code Gen Strategy
I like to break up code into different categories: **Primitives, Components, Services**.

### Primitives
Primitives are small discrete functions that are 20 lines or less.
They're focused on a specific task and can be easily unit tested.
I like to keep "state" out of these functions.
These are mechanical operators.

Examples:
- function to calc the opening range breakout

### Components
Components are collections of Primitives that evolved to manage a process from start to finish.
They're often classes and keep track of "state", but I can still test 90% of what's important with unit tests.

### Services
Services are the harness for creating long-running processes that are healthy and operate well.
