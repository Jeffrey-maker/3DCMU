import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphNode } from "../types/graph";

interface RoomPickerProps {
  label: string;
  rooms: GraphNode[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string | null) => void;
  showFloor?: boolean;
}

const ANY = "__any__";

const TYPE_SUFFIX: Record<string, string> = {
  stair: " (stairs)",
  elevator: " (elevator)",
};

/**
 * Room numbers sort as numbers, not as text: plain string ordering puts
 * "4101" after "41" and scatters a suite's "4201A"/"4201B" away from
 * "4201". Compare the numeric part numerically and the trailing letters
 * after it, so the list reads the way the building is numbered.
 */
function compareRoomLabels(a: string, b: string): number {
  const parse = (s: string) => {
    const m = /^(\d+)(.*)$/.exec(s.trim());
    return m ? { num: Number(m[1]), rest: m[2] } : { num: Number.POSITIVE_INFINITY, rest: s };
  };
  const pa = parse(a);
  const pb = parse(b);
  if (pa.num !== pb.num) return pa.num - pb.num;
  return pa.rest.localeCompare(pb.rest);
}

export function RoomPicker({ label, rooms, selectedNodeId, onSelect, showFloor = false }: RoomPickerProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [building, setBuilding] = useState(ANY);
  const [floor, setFloor] = useState(ANY);
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const buildings = useMemo(
    () => [...new Set(rooms.map((r) => r.building))].sort(),
    [rooms],
  );
  // Floors offered follow the building already chosen, so the two filters
  // can't be combined into something that matches nothing.
  const floors = useMemo(() => {
    const scope = building === ANY ? rooms : rooms.filter((r) => r.building === building);
    return [...new Set(scope.map((r) => r.floor))].sort((a, b) => a - b);
  }, [rooms, building]);

  const scoped = useMemo(
    () =>
      rooms.filter(
        (r) =>
          (building === ANY || r.building === building) &&
          (floor === ANY || String(r.floor) === floor),
      ),
    [rooms, building, floor],
  );

  const sorted = useMemo(() => {
    const copy = [...scoped];
    copy.sort((a, b) => {
      if (showFloor && a.floor !== b.floor) return a.floor - b.floor;
      return compareRoomLabels(a.label ?? a.id, b.label ?? b.id);
    });
    return copy;
  }, [scoped, showFloor]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter((r) => {
      const text = `${r.label ?? r.id} ${r.room_type ?? ""} ${showFloor ? `floor ${r.floor}` : ""}`;
      return text.toLowerCase().includes(q);
    });
  }, [sorted, query, showFloor]);

  const selected = useMemo(
    () => rooms.find((r) => r.id === selectedNodeId) ?? null,
    [rooms, selectedNodeId],
  );

  // Close when focus leaves the whole control, not just the input -- clicking
  // an option must not be read as leaving.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as globalThis.Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (!open || !listRef.current) return;
    listRef.current.children[active]?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  function describe(room: GraphNode): string {
    const floor = showFloor ? `F${room.floor} · ` : "";
    return `${floor}${room.label ?? room.id}${TYPE_SUFFIX[room.type] ?? ""}`;
  }

  function choose(room: GraphNode) {
    onSelect(room.id);
    setQuery("");
    setOpen(false);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) setOpen(true);
      setActive((i) => {
        const next = event.key === "ArrowDown" ? i + 1 : i - 1;
        return Math.max(0, Math.min(filtered.length - 1, next));
      });
    } else if (event.key === "Enter") {
      if (open && filtered[active]) {
        event.preventDefault();
        choose(filtered[active]);
      }
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="room-picker" ref={containerRef}>
      <label>{label}</label>
      {(buildings.length > 1 || floors.length > 1) && (
        <div className="room-picker-filters">
          {buildings.length > 1 && (
            <select
              aria-label={`${label} building`}
              value={building}
              onChange={(e) => {
                setBuilding(e.target.value);
                setFloor(ANY);
              }}
            >
              <option value={ANY}>All buildings</option>
              {buildings.map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
          )}
          <select
            aria-label={`${label} floor`}
            value={floor}
            onChange={(e) => setFloor(e.target.value)}
          >
            <option value={ANY}>All floors</option>
            {floors.map((f) => (
              <option key={f} value={String(f)}>Floor {f}</option>
            ))}
          </select>
        </div>
      )}
      <div className="room-picker-control">
        <input
          type="text"
          role="combobox"
          aria-expanded={open}
          placeholder={selected ? describe(selected) : "Search or pick a room..."}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className={selected && !query ? "has-selection" : ""}
        />
        {selected && (
          <button
            type="button"
            className="room-picker-clear"
            aria-label={`Clear ${label}`}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              onSelect(null);
              setQuery("");
            }}
          >
            ×
          </button>
        )}
        <button
          type="button"
          className="room-picker-toggle"
          aria-label={open ? "Close list" : "Show all rooms"}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => setOpen((v) => !v)}
        >
          ▾
        </button>
      </div>
      {open && (
        <ul className="room-picker-list" ref={listRef} role="listbox">
          {filtered.length === 0 && <li className="room-picker-empty">No match</li>}
          {filtered.map((room, index) => (
            <li
              key={room.id}
              role="option"
              aria-selected={room.id === selectedNodeId}
              className={
                (room.id === selectedNodeId ? "selected " : "") + (index === active ? "active" : "")
              }
              onMouseEnter={() => setActive(index)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(room)}
            >
              <span className="room-picker-label">{describe(room)}</span>
              {room.room_type && <span className="room-picker-type">{room.room_type}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
