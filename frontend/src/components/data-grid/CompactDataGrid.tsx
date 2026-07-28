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
import { useCallback, useEffect, useMemo, useRef } from "react";

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
  testId?: string;
}

export function CompactDataGrid<TData extends { id: string }>({
  ariaLabel,
  columns,
  rows,
  quickFilterText,
  selectedId,
  onRowClick,
  testId,
}: CompactDataGridProps<TData>) {
  const gridRef = useRef<AgGridReact<TData>>(null);

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

  return (
    <div className="h-full min-h-0 overflow-hidden rounded-lg border border-border bg-surface-1" data-testid={testId}>
      <AgGridReact<TData>
        ref={gridRef}
        aria-label={ariaLabel}
        theme={compactDarkTheme}
        rowData={rows}
        columnDefs={columns}
        defaultColDef={defaultColDef}
        getRowId={(params) => params.data.id}
        getRowClass={(params) => (params.data?.id === selectedId ? "compact-data-grid-row-selected" : "")}
        rowSelection={{ mode: "singleRow", checkboxes: false, enableClickSelection: true }}
        quickFilterText={quickFilterText}
        pagination
        paginationPageSize={12}
        paginationPageSizeSelector={[12, 24, 48]}
        rowHeight={34}
        headerHeight={32}
        animateRows={false}
        onGridReady={selectCurrentRow}
        onRowDataUpdated={() => selectCurrentRow()}
        onCellClicked={handleCellClicked}
      />
    </div>
  );
}
