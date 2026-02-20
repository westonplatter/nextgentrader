import { useState } from "react";
import AccountsTable from "./components/AccountsTable";
import OrdersTable from "./components/OrdersTable";
import PositionsTable from "./components/PositionsTable";
import TradebotChat from "./components/TradebotChat";
import WorkerStatusLights from "./components/WorkerStatusLights";

type Page = "positions" | "accounts" | "orders" | "tradebot";

function App() {
  const [page, setPage] = useState<Page>("tradebot");
  const horizontalPaddingClass = page === "tradebot" ? "px-2 md:px-3" : "px-6";
  const contentClass =
    page === "tradebot"
      ? `${horizontalPaddingClass} py-3 flex-1 min-h-0 overflow-y-auto lg:overflow-hidden`
      : `${horizontalPaddingClass} py-6`;

  return (
    <div className="w-full min-h-screen flex flex-col">
      <nav
        className={`flex items-center gap-6 ${horizontalPaddingClass} py-3 border-b border-gray-200 bg-white`}
      >
        <span className="font-bold text-lg">ngtrader</span>
        <button
          onClick={() => setPage("positions")}
          className={`text-sm ${page === "positions" ? "text-black font-semibold" : "text-gray-500 hover:text-gray-800"}`}
        >
          Positions
        </button>
        <button
          onClick={() => setPage("accounts")}
          className={`text-sm ${page === "accounts" ? "text-black font-semibold" : "text-gray-500 hover:text-gray-800"}`}
        >
          Accounts
        </button>
        <button
          onClick={() => setPage("orders")}
          className={`text-sm ${page === "orders" ? "text-black font-semibold" : "text-gray-500 hover:text-gray-800"}`}
        >
          Orders
        </button>
        <button
          onClick={() => setPage("tradebot")}
          className={`text-sm ${page === "tradebot" ? "text-black font-semibold" : "text-gray-500 hover:text-gray-800"}`}
        >
          Tradebot
        </button>
        <WorkerStatusLights />
      </nav>
      <div className={contentClass}>
        {page === "positions" && <PositionsTable />}
        {page === "accounts" && <AccountsTable />}
        {page === "orders" && <OrdersTable />}
        {page === "tradebot" && <TradebotChat />}
      </div>
    </div>
  );
}

export default App;
