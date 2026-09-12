import { useMemo, useState } from "react";
import type { GraphNode } from "../types/graph";

interface RoomPickerProps {
  label: string;
  rooms: GraphNode[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string | null) => void;
}

// Plain filtered <input> + <select> -- no combobox library needed at the
// ~50-100 room scale this app targets.
export function RoomPicker({ label, rooms, selectedNodeId, onSelect }: RoomPickerProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rooms;
    return rooms.filter((r) => (r.label ?? r.id).toLowerCase().includes(q));
  }, [rooms, query]);

  return (
    <div className="room-picker">
      <label>{label}</label>
      <input
        type="text"
        placeholder="Search room number..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <select size={6} value={selectedNodeId ?? ""} onChange={(e) => onSelect(e.target.value || null)}>
        {filtered.map((r) => (
          <option key={r.id} value={r.id}>
            Room {r.label ?? r.id}
            {r.room_type ? ` — ${r.room_type}` : ""}
          </option>
        ))}
      </select>
    </div>
  );
}
