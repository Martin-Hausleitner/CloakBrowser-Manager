import {
  ClientSideRowModelModule,
  ModuleRegistry,
  NumberFilterModule,
  PaginationModule,
  QuickFilterModule,
  RowApiModule,
  RowSelectionModule,
  RowStyleModule,
  TextFilterModule,
  themeQuartz,
} from "ag-grid-community";
import type { CellClickedEvent, ColDef, GridReadyEvent } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { Columns3, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

ModuleRegistry.registerModules([
  ClientSideRowModelModule,
  NumberFilterModule,
  PaginationModule,
  QuickFilterModule,
  RowApiModule,
  RowSelectionModule,
  RowStyleModule,
  TextFilterModule,
]);

const compactDarkTheme = themeQuartz.withParams({
  accentColor: "#60a5fa",
  backgroundColor: "#111113",
  borderColor: "#27272a",
  browserColorScheme: "dark",
  cellTextColor: "#d4d4d8",
  chromeBackgroundColor: "#18181b",
  fontFamily: "Avenir Next, SF Pro Text, Helvetica Neue, system-ui, sans-serif",
  fontSize: 12,
  foregroundColor: "#e5e7eb",
  headerBackgroundColor: "#18181b",
  headerFontSize: 11,
  headerTextColor: "#a1a1aa",
  oddRowBackgroundColor: "#151518",
  rowBorder: { color: "#242428" },
  selectedRowBackgroundColor: "rgba(96, 165, 250, 0.14)",
  spacing: 4,
  wrapperBorderRadius: 8,
});

export interface CompactDataGridProps<TData extends { id: string }> {
  ariaLabel: string;
  columns: ColDef<TData>[];
  rows: TData[];
  quickFilterText: string;
  selectedId: string | null;
  onRowClick: (row: TData) => void;
  storageKey: string;
  testId?: string;
}

type GridDensity = "compact" | "comfortable" | "audit";

interface StoredGridView {
  density: GridDensity;
  hiddenColumns: string[];
}

const GRID_VIEW_STORAGE_PREFIX = "cloakbrowser.table-view.v1";

const DENSITY_OPTIONS: Record<GridDensity, { headerHeight: number; rowHeight: number; pageSize: number }> = {
  compact: { headerHeight: 30, rowHeight: 32, pageSize: 16 },
  comfortable: { headerHeight: 36, rowHeight: 42, pageSize: 12 },
  audit: { headerHeight: 28, rowHeight: 28, pageSize: 24 },
};

function columnKey<TData>(column: ColDef<TData>, index: number) {
  return String(column.colId ?? column.field ?? column.headerName ?? `column-${index}`);
}

function readStoredView(storageKey: string): StoredGridView {
  if (typeof window === "undefined") return { density: "compact", hiddenColumns: [] };
  try {
    const raw = window.localStorage.getItem(`${GRID_VIEW_STORAGE_PREFIX}.${storageKey}`);
    if (!raw) return { density: "compact", hiddenColumns: [] };
    const parsed = JSON.parse(raw) as Partial<StoredGridView>;
    const density = parsed.density;
    return {
      density: density === "comfortable" || density === "audit" ? density : "compact",
      hiddenColumns: Array.isArray(parsed.hiddenColumns)
        ? parsed.hiddenColumns.filter((key): key is string => typeof key === "string")
        : [],
    };
  } catch {
    return { density: "compact", hiddenColumns: [] };
  }
}

export function CompactDataGrid<TData extends { id: string }>({
  ariaLabel,
  columns,
  rows,
  quickFilterText,
  selectedId,
  onRowClick,
  storageKey,
  testId,
}: CompactDataGridProps<TData>) {
  const gridRef = useRef<AgGridReact<TData>>(null);
  const initialView = useMemo(() => readStoredView(storageKey), [storageKey]);
  const [density, setDensity] = useState<GridDensity>(initialView.density);
  const [hiddenColumns, setHiddenColumns] = useState<string[]>(initialView.hiddenColumns);
  const [columnsOpen, setColumnsOpen] = useState(false);

  const columnOptions = useMemo(
    () => columns.map((column, index) => ({
      key: columnKey(column, index),
      label: column.headerName ?? String(column.field ?? `Column ${index + 1}`),
    })),
    [columns],
  );

  const visibleColumns = useMemo(
    () => columns.map((column, index) => ({
      ...column,
      hide: hiddenColumns.includes(columnKey(column, index)),
    })),
    [columns, hiddenColumns],
  );

  const densityConfig = DENSITY_OPTIONS[density];

  useEffect(() => {
    try {
      window.localStorage.setItem(
        `${GRID_VIEW_STORAGE_PREFIX}.${storageKey}`,
        JSON.stringify({ density, hiddenColumns } satisfies StoredGridView),
      );
    } catch {
      // Browsing can continue when storage is unavailable or full.
    }
  }, [density, hiddenColumns, storageKey]);

  const defaultColDef = useMemo<ColDef<TData>>(
    () => ({
      filter: true,
      minWidth: 96,
      resizable: true,
      sortable: true,
    }),
    [],
  );

  const selectCurrentRow = useCallback(
    (event?: GridReadyEvent<TData>) => {
      const api = event?.api ?? gridRef.current?.api;
      if (!api) return;
      api.forEachNode((node) => {
        node.setSelected(Boolean(selectedId && node.data?.id === selectedId));
      });
    },
    [selectedId],
  );

  const handleCellClicked = useCallback(
    (event: CellClickedEvent<TData>) => {
      if (event.data && event.colDef.headerName !== "Actions") onRowClick(event.data);
    },
    [onRowClick],
  );

  useEffect(() => {
    selectCurrentRow();
  }, [selectCurrentRow]);

  const resetView = useCallback(() => {
    setDensity("compact");
    setHiddenColumns([]);
    setColumnsOpen(false);
    try {
      window.localStorage.removeItem(`${GRID_VIEW_STORAGE_PREFIX}.${storageKey}`);
    } catch {
      // Browsing can continue when storage is unavailable.
    }
  }, [storageKey]);

  return (
    <div
      className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-surface-1"
      data-density={density}
      data-testid={testId}
    >
      <div className="flex min-h-9 shrink-0 items-center gap-1.5 border-b border-border bg-surface-0 px-2 py-1">
        <span className="mr-auto text-[11px] tabular-nums text-gray-500">
          {rows.length} {rows.length === 1 ? "row" : "rows"}
        </span>
        <label className="sr-only" htmlFor={`${storageKey}-grid-density`}>Grid density</label>
        <select
          id={`${storageKey}-grid-density`}
          className="h-7 rounded border border-border bg-surface-2 px-2 text-[11px] text-gray-300 outline-none focus:ring-2 focus:ring-accent/40"
          value={density}
          onChange={(event) => setDensity(event.target.value as GridDensity)}
          aria-label="Grid density"
        >
          <option value="compact">Compact</option>
          <option value="comfortable">Comfortable</option>
          <option value="audit">Audit</option>
        </select>
        <button
          type="button"
          className="inline-flex h-7 items-center gap-1 rounded border border-border bg-surface-2 px-2 text-[11px] text-gray-300 hover:bg-surface-3"
          aria-expanded={columnsOpen}
          onClick={() => setColumnsOpen((open) => !open)}
        >
          <Columns3 className="h-3 w-3" />
          Columns
        </button>
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded border border-border bg-surface-2 text-gray-400 hover:bg-surface-3 hover:text-gray-200"
          aria-label="Reset table view"
          title="Reset table view"
          onClick={resetView}
        >
          <RotateCcw className="h-3 w-3" />
        </button>
      </div>

      {columnsOpen ? (
        <div className="absolute right-9 top-9 z-20 max-h-72 min-w-48 overflow-y-auto rounded-lg border border-border bg-surface-2 p-2 shadow-xl">
          <div className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wider text-gray-500">Visible columns</div>
          {columnOptions.map((column) => (
            <label key={column.key} className="flex min-h-7 cursor-pointer items-center gap-2 rounded px-1.5 text-xs text-gray-300 hover:bg-surface-3">
              <input
                type="checkbox"
                checked={!hiddenColumns.includes(column.key)}
                onChange={(event) => setHiddenColumns((current) => (
                  event.target.checked
                    ? current.filter((key) => key !== column.key)
                    : [...current, column.key]
                ))}
                aria-label={`Show ${column.label}`}
              />
              <span className="truncate">{column.label}</span>
            </label>
          ))}
        </div>
      ) : null}

      <div className="min-h-0 flex-1">
        <AgGridReact<TData>
          ref={gridRef}
          aria-label={ariaLabel}
          theme={compactDarkTheme}
          rowData={rows}
          columnDefs={visibleColumns}
          defaultColDef={defaultColDef}
          getRowId={(params) => params.data.id}
          getRowClass={(params) => (params.data?.id === selectedId ? "compact-data-grid-row-selected" : "")}
          rowSelection={{ mode: "singleRow", checkboxes: false, enableClickSelection: true }}
          quickFilterText={quickFilterText}
          pagination
          paginationPageSize={densityConfig.pageSize}
          paginationPageSizeSelector={[12, 16, 24, 48]}
          rowHeight={densityConfig.rowHeight}
          headerHeight={densityConfig.headerHeight}
          animateRows={false}
          onGridReady={selectCurrentRow}
          onRowDataUpdated={() => selectCurrentRow()}
          onCellClicked={handleCellClicked}
        />
      </div>
    </div>
  );
}
