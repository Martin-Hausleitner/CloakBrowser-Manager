import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { ProfileViewer } from "./ProfileViewer";

const rfbMock = vi.hoisted(() => {
  const instances: MockRFB[] = [];

  class MockRFB extends EventTarget {
    scaleViewport = false;
    resizeSession = true;
    showDotCursor = false;
    viewOnly = false;
    disconnectCalls = 0;
    sendKeyCalls: unknown[][] = [];
    _display = {
      width: 1024,
      height: 576,
      autoscale: vi.fn(),
      _damage: vi.fn(),
      flip: vi.fn(),
    };

    constructor(
      public target: HTMLElement,
      public url: string,
      public options: unknown,
    ) {
      super();
      instances.push(this);
    }

    disconnect() {
      this.disconnectCalls += 1;
      this.dispatchEvent(new Event("disconnect"));
    }

    sendKey(...args: unknown[]) {
      this.sendKeyCalls.push(args);
    }

    emit(type: string, detail?: unknown) {
      this.dispatchEvent(new CustomEvent(type, { detail }));
    }
  }

  return { instances, MockRFB };
});

const apiMock = vi.hoisted(() => ({
  getClipboard: vi.fn(),
  setClipboard: vi.fn(),
}));

vi.mock("@novnc/novnc/core/rfb.js", () => ({
  default: rfbMock.MockRFB,
}));

vi.mock("../lib/api", () => ({
  api: apiMock,
}));

const reconnectDelays = [500, 1000, 2000, 5000, 10000];

async function flushAsyncWork() {
  await act(async () => {
    await Promise.resolve();
  });
}

function setVisibilityState(value: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value,
  });
}

function setCoarsePointer(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches,
      media: "(pointer: coarse)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

async function renderProfileViewer(onDisconnect = vi.fn()) {
  const view = render(
    <ProfileViewer
      profileId="profile-1"
      cdpUrl={null}
      clipboardSync={true}
      onDisconnect={onDisconnect}
    />,
  );

  await flushAsyncWork();
  expect(rfbMock.instances).toHaveLength(1);
  return { onDisconnect, ...view };
}

describe("ProfileViewer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    rfbMock.instances.length = 0;
    apiMock.getClipboard.mockClear();
    apiMock.setClipboard.mockClear();
    apiMock.getClipboard.mockResolvedValue({ text: "" });
    apiMock.setClipboard.mockResolvedValue({ ok: true });
    setVisibilityState("visible");
    setCoarsePointer(false);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        readText: vi.fn().mockResolvedValue(""),
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("keeps the viewer in reconnecting state after a transient noVNC disconnect", async () => {
    const { onDisconnect } = await renderProfileViewer();

    act(() => rfbMock.instances[0]?.emit("connect"));
    expect(screen.getByText("Connected")).toBeTruthy();

    act(() => rfbMock.instances[0]?.emit("disconnect"));

    expect(onDisconnect).not.toHaveBeenCalled();
    expect(screen.getByText("Reconnecting...")).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(499);
    });
    expect(rfbMock.instances).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    await flushAsyncWork();
    expect(rfbMock.instances).toHaveLength(2);
    expect(onDisconnect).not.toHaveBeenCalled();
  });

  it("uses browser lifecycle events to reconnect before the backoff timer fires", async () => {
    const { onDisconnect } = await renderProfileViewer();

    act(() => rfbMock.instances[0]?.emit("connect"));
    act(() => rfbMock.instances[0]?.emit("disconnect"));
    act(() => window.dispatchEvent(new Event("online")));

    await flushAsyncWork();
    expect(rfbMock.instances).toHaveLength(2);
    expect(onDisconnect).not.toHaveBeenCalled();
  });

  it("does not notify or reconnect after component cleanup", async () => {
    const onDisconnect = vi.fn();
    const { unmount } = render(
      <ProfileViewer
        profileId="profile-1"
        cdpUrl={null}
        clipboardSync={true}
        onDisconnect={onDisconnect}
      />,
    );
    await flushAsyncWork();
    expect(rfbMock.instances).toHaveLength(1);

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });

    expect(rfbMock.instances[0]?.disconnectCalls).toBe(1);
    expect(rfbMock.instances).toHaveLength(1);
    expect(onDisconnect).not.toHaveBeenCalled();
  });

  it("notifies the parent only after reconnect attempts are exhausted", async () => {
    const { onDisconnect } = await renderProfileViewer();

    act(() => rfbMock.instances[0]?.emit("connect"));
    act(() => rfbMock.instances[0]?.emit("disconnect"));

    for (const [index, delay] of reconnectDelays.entries()) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(delay);
      });
      await flushAsyncWork();
      expect(rfbMock.instances).toHaveLength(index + 2);
      act(() => rfbMock.instances.at(-1)?.emit("disconnect"));
    }

    expect(onDisconnect).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Connection failed")).toBeTruthy();
    expect(screen.getByText("Connection lost after repeated reconnect attempts.")).toBeTruthy();
  });

  it("treats security failures as terminal disconnects", async () => {
    const { onDisconnect } = await renderProfileViewer();

    act(() => rfbMock.instances[0]?.emit("securityfailure", { reason: "bad auth" }));

    expect(onDisconnect).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Connection failed")).toBeTruthy();
    expect(screen.getByText("Security failure: bad auth")).toBeTruthy();
  });

  it("marks a scoped viewer as read-only and hides clipboard input controls", async () => {
    render(
      <ProfileViewer
        profileId="profile-1"
        cdpUrl={null}
        clipboardSync={true}
        canInteract={false}
        onDisconnect={vi.fn()}
      />,
    );
    await flushAsyncWork();

    expect(rfbMock.instances[0]?.viewOnly).toBe(true);
    expect(screen.getByText("View only")).toBeTruthy();
    expect(screen.queryByLabelText("Paste text into remote browser")).toBeNull();
    expect(screen.queryByLabelText("Enable clipboard sync")).toBeNull();
  });

  it("focuses the dynamically created noVNC canvas on pointer interaction", async () => {
    await renderProfileViewer();
    act(() => rfbMock.instances[0]?.emit("connect"));

    const container = rfbMock.instances[0]!.target;
    const canvas = document.createElement("canvas");
    container.appendChild(canvas);

    fireEvent.pointerDown(canvas);

    expect(canvas.getAttribute("tabindex")).toBe("-1");
    expect(document.activeElement).toBe(canvas);
  });

  it("focuses the dynamically created noVNC canvas on touch interaction", async () => {
    await renderProfileViewer();
    act(() => rfbMock.instances[0]?.emit("connect"));

    const container = rfbMock.instances[0]!.target;
    const canvas = document.createElement("canvas");
    container.appendChild(canvas);

    fireEvent.touchStart(canvas);

    expect(canvas.getAttribute("tabindex")).toBe("-1");
    expect(document.activeElement).toBe(canvas);
  });

  it("does not focus noVNC canvas interactions in view-only mode", async () => {
    render(
      <ProfileViewer
        profileId="profile-1"
        cdpUrl={null}
        clipboardSync={true}
        canInteract={false}
        onDisconnect={vi.fn()}
      />,
    );
    await flushAsyncWork();

    const container = rfbMock.instances[0]!.target;
    const canvas = document.createElement("canvas");
    container.appendChild(canvas);

    fireEvent.pointerDown(canvas);

    expect(canvas.hasAttribute("tabindex")).toBe(false);
    expect(document.activeElement).not.toBe(canvas);
  });

  it("repaints the noVNC canvas after its container is resized", async () => {
    const originalResizeObserver = window.ResizeObserver;
    let resizeCallback: ResizeObserverCallback | null = null;
    const observe = vi.fn();
    const disconnect = vi.fn();

    class MockResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = callback;
      }

      observe = observe;
      unobserve = vi.fn();
      disconnect = disconnect;
    }

    Object.defineProperty(window, "ResizeObserver", {
      configurable: true,
      value: MockResizeObserver,
    });

    const view = await renderProfileViewer();
    expect(observe).toHaveBeenCalledTimes(1);
    vi.spyOn(observe.mock.calls[0][0] as HTMLElement, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      width: 390,
      height: 713,
      top: 0,
      right: 390,
      bottom: 713,
      left: 0,
      toJSON: () => ({}),
    });

    act(() => resizeCallback?.([], {} as ResizeObserver));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });

    const instance = rfbMock.instances[0];
    expect(instance?._display.autoscale).toHaveBeenCalledTimes(1);
    expect(instance?._display._damage).toHaveBeenCalledWith(0, 0, 1024, 576);
    expect(instance?._display.flip).toHaveBeenCalledTimes(1);

    view.unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);
    Object.defineProperty(window, "ResizeObserver", {
      configurable: true,
      value: originalResizeObserver,
    });
  });

  it("pauses clipboard polling while hidden", async () => {
    setVisibilityState("hidden");
    await renderProfileViewer();

    act(() => rfbMock.instances[0]?.emit("connect"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(api.getClipboard).not.toHaveBeenCalled();
  });

  it("keeps clipboard sync and manual paste available on coarse-pointer devices", async () => {
    setCoarsePointer(true);
    await renderProfileViewer();

    act(() => rfbMock.instances[0]?.emit("connect"));
    expect(
      (screen.getByRole("button", { name: "Disable clipboard sync" }) as HTMLButtonElement).disabled,
    ).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Paste text into remote browser" }));
    fireEvent.change(screen.getByLabelText("Paste text into the remote browser"), {
      target: { value: "hello from an iPhone" },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send pasted text to remote browser" }));
      await Promise.resolve();
    });

    expect(api.setClipboard).toHaveBeenCalledWith("profile-1", "hello from an iPhone");
    expect(rfbMock.instances[0]?.sendKeyCalls).toEqual([
      [0xffe3, "ControlLeft", true],
      [0x0076, "KeyV", true],
      [0x0076, "KeyV", false],
      [0xffe3, "ControlLeft", false],
    ]);
  });

  it("keeps manual paste available when the browser clipboard API is unavailable", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    setCoarsePointer(true);
    await renderProfileViewer();

    act(() => rfbMock.instances[0]?.emit("connect"));
    expect(
      (screen.getByRole("button", { name: "Enable clipboard sync" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Paste text into remote browser" }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("forwards Ctrl+V to VNC when browser clipboard permission is denied", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        readText: vi.fn().mockRejectedValue(new Error("clipboard permission denied")),
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
    await renderProfileViewer();

    act(() => rfbMock.instances[0]?.emit("connect"));
    const vncContainer = document.querySelector("[data-vnc-layout]");
    expect(vncContainer).toBeTruthy();

    await act(async () => {
      fireEvent.keyDown(vncContainer!, { key: "v", code: "KeyV", ctrlKey: true });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.setClipboard).not.toHaveBeenCalled();
    expect(rfbMock.instances[0]?.sendKeyCalls).toEqual([
      [0xffe3, "ControlLeft", true],
      [0x0076, "KeyV", true],
      [0x0076, "KeyV", false],
      [0xffe3, "ControlLeft", false],
    ]);
    expect(screen.getByRole("button", { name: "Enable clipboard sync" })).toBeTruthy();
  });
});
