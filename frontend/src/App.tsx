import { useState } from "react";
import AccountsTable from "./components/AccountsTable";
import PositionsTable from "./components/PositionsTable";

type Page = "positions" | "accounts";

function App() {
  const [page, setPage] = useState<Page>("positions");

  return (
    <div className="w-full">
      <nav className="flex items-center gap-6 px-6 py-3 border-b border-gray-200 bg-white">
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
      </nav>
      <div className="px-6 py-6">
        {page === "positions" && <PositionsTable />}
        {page === "accounts" && <AccountsTable />}
      </div>
    </div>
  );
}

export default App;
