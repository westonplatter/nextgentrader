import { useEffect, useState } from "react";

interface Order {
  id: number;
  account_id: number;
  account_alias: string | null;
  symbol: string;
  sec_type: string;
  side: string;
  quantity: number;
  status: string;
  contract_month: string | null;
  local_symbol: string | null;
  ib_order_id: number | null;
  filled_quantity: number;
  avg_fill_price: number | null;
  created_at: string;
  updated_at: string;
}

const COLUMNS: { key: keyof Order; label: string }[] = [
  { key: "id", label: "Order ID" },
  { key: "account_alias", label: "Account" },
  { key: "symbol", label: "Symbol" },
  { key: "sec_type", label: "Sec Type" },
  { key: "side", label: "Side" },
  { key: "quantity", label: "Qty" },
  { key: "status", label: "Status" },
  { key: "contract_month", label: "Contract Month" },
  { key: "local_symbol", label: "Local Symbol" },
  { key: "ib_order_id", label: "IB Order ID" },
  { key: "filled_quantity", label: "Filled Qty" },
  { key: "avg_fill_price", label: "Avg Fill Px" },
  { key: "created_at", label: "Created At" },
  { key: "updated_at", label: "Updated At" },
];

export default function OrdersTable() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actioning, setActioning] = useState<Set<number>>(new Set());

  useEffect(() => {
    let active = true;

    const load = () => {
      fetch("http://localhost:8000/api/v1/orders")
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data: Order[]) => {
          if (!active) return;
          setOrders(data);
          setError(null);
        })
        .catch((err: Error) => {
          if (!active) return;
          setError(err.message);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    };

    load();
    const timer = window.setInterval(load, 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  function cancelOrder(orderId: number) {
    setActioning((prev) => {
      const next = new Set(prev);
      next.add(orderId);
      return next;
    });
    fetch(`http://localhost:8000/api/v1/orders/${orderId}/cancel`, { method: "POST" })
      .then(async (res) => {
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      })
      .then((updated: Order) => {
        setOrders((prev) => prev.map((row) => (row.id === updated.id ? updated : row)));
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => {
        setActioning((prev) => {
          const next = new Set(prev);
          next.delete(orderId);
          return next;
        });
      });
  }

  if (loading) return <p className="text-gray-500">Loading orders...</p>;
  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (orders.length === 0) return <p className="text-gray-500">No orders found.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full border-collapse text-sm">
        <thead>
          <tr className="bg-gray-100 text-left">
            {COLUMNS.map((col) => (
              <th key={col.key} className="px-3 py-2 font-semibold text-gray-700 whitespace-nowrap">
                {col.label}
              </th>
            ))}
            <th className="px-3 py-2 font-semibold text-gray-700 whitespace-nowrap">Actions</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr key={order.id} className="border-b border-gray-200 hover:bg-gray-50">
              {COLUMNS.map((col) => (
                <td key={col.key} className="px-3 py-2 whitespace-nowrap">
                  {order[col.key] ?? "—"}
                </td>
              ))}
              <td className="px-3 py-2 whitespace-nowrap">
                {order.status === "queued" && (
                  <button
                    onClick={() => cancelOrder(order.id)}
                    disabled={actioning.has(order.id)}
                    className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
