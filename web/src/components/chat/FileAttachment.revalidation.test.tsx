// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FileAttachment } from "./FileAttachment";
import { describeAttachment } from "@/lib/chat-attachments";
import { authedFetch } from "@/lib/api";
vi.mock("@/lib/chat-attachments", async original => ({ ...await original<typeof import("@/lib/chat-attachments")>(), describeAttachment: vi.fn() }));
vi.mock("@/lib/api", async original => ({ ...await original<typeof import("@/lib/api")>(), authedFetch: vi.fn() }));
let host: HTMLDivElement; let root: Root;
const file = { path: "/workspace/test.png", name: "test.png", size: 24, kind: "png", reader: "image", revision: "first" };
beforeEach(() => {
  vi.useFakeTimers(); vi.mocked(describeAttachment).mockReset().mockResolvedValue(file);
  vi.mocked(authedFetch).mockReset().mockResolvedValue(new Response(new Blob(["synthetic"])));
  window.__HERMES_SESSION_TOKEN__ = 'test-only';
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn().mockReturnValue('blob:test'), revokeObjectURL: vi.fn() }));
  host=document.createElement('div');document.body.append(host);root=createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount());host.remove();vi.useRealTimers();vi.unstubAllGlobals();delete window.__HERMES_SESSION_TOKEN__; });
async function render() { await act(async () => root.render(<FileAttachment path={file.path} />)); }
async function interval() { await act(async () => { await vi.advanceTimersByTimeAsync(30_000); }); }
it('revalidates metadata without replacing the image or downloading unchanged bytes', async () => {
  await render();const img=host.querySelector('img');expect(img).not.toBeNull();
  await interval();expect(describeAttachment).toHaveBeenCalledTimes(2);
  expect(host.querySelector('img')).toBe(img);expect(authedFetch).toHaveBeenCalledOnce();
});
it('downloads new bytes only when revision changes, releasing the old URL', async () => {
  await render();vi.mocked(describeAttachment).mockResolvedValue({ ...file, revision: 'second' });
  await interval();expect(authedFetch).toHaveBeenCalledTimes(2);expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test');
});
it('removes a revoked file and closes its viewer', async () => {
  await render();await act(async () => host.querySelector('button')!.click());
  expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  vi.mocked(describeAttachment).mockRejectedValue(new Error('403: {"detail":"Доступ отозван"}'));
  await interval();expect(host.querySelector('img')).toBeNull();expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(host.textContent).toContain('Доступ отозван');expect(host.querySelector('a[download]')).toBeNull();
});
it('keeps already displayed bytes when the network is temporarily unavailable', async () => {
  await render();const img=host.querySelector('img');vi.mocked(describeAttachment).mockRejectedValue(new Error('Failed to fetch'));
  await interval();expect(host.querySelector('img')).toBe(img);
});
