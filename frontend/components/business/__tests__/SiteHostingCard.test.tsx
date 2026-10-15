import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const upload = vi.fn();
const publish = vi.fn();
const addDomain = vi.fn();
const verify = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  apiBaseUrl: "https://api.example.test",
  getSiteHosting: (...a: unknown[]) => get(...a),
  uploadHostedSite: (...a: unknown[]) => upload(...a),
  publishHostedSite: (...a: unknown[]) => publish(...a),
  unpublishHostedSite: vi.fn(),
  addHostedDomain: (...a: unknown[]) => addDomain(...a),
  verifyHostedDomain: (...a: unknown[]) => verify(...a),
  removeHostedDomain: vi.fn(),
}));
import { SiteHostingCard } from "@/components/business/SiteHostingCard";

const pending = { id: "d1", hostname: "www.example.com", status: "PENDING", verified_at: null, txt_name: "_klaros-verify.www.example.com", txt_value: "klaros-abc", dns_target: { a_record: "76.76.21.21", cname: "cname.vercel-dns.com" }, edge_status: "WAITING_FOR_DNS", edge_detail: null, automated: true };
const draft = { id: "u1", version: 1, status: "DRAFT", source_name: "site.zip", file_count: 4, total_bytes: 2048, created_at: null, published_at: null };

describe("SiteHostingCard", () => {
  beforeEach(() => {
    [get, upload, publish, addDomain, verify].forEach((m) => m.mockReset());
    get.mockResolvedValue({ tenant_id: "tenant-1", uploads: [draft], domains: [pending] });
  });

  it("shows the DNS record to add and the enquiry snippet for this business", async () => {
    render(<SiteHostingCard token="t" />);
    expect(await screen.findByText(/_klaros-verify\.www\.example\.com/)).toBeInTheDocument();
    expect(screen.getByText(/klaros-abc/)).toBeInTheDocument();
    expect(screen.getByText(/https:\/\/api\.example\.test\/api\/v1\/public\/leads\/tenant-1/)).toBeInTheDocument();
    expect(screen.getByText("Waiting for your DNS records")).toBeInTheDocument();
  });

  it("publishes a draft", async () => {
    publish.mockResolvedValue({ ...draft, status: "PUBLISHED" });
    render(<SiteHostingCard token="t" />);
    fireEvent.click(await screen.findByRole("button", { name: "Publish" }));
    await waitFor(() => expect(publish).toHaveBeenCalledWith("t", "u1"));
  });

  it("says plainly when the DNS record is not visible yet, and never claims it is verified", async () => {
    verify.mockResolvedValue({ verified: false, domain: pending });
    render(<SiteHostingCard token="t" />);
    fireEvent.click(await screen.findByRole("button", { name: "Check now" }));
    expect(await screen.findByText(/could not see the DNS record yet/)).toBeInTheDocument();
    expect(screen.queryByText(/www\.example\.com is verified/)).not.toBeInTheDocument();
  });

  it("tells the owner nothing else is needed when setup is automatic, and what to do by hand when it is not", async () => {
    const { unmount } = render(<SiteHostingCard token="t" />);
    expect(await screen.findByText(/it goes live by itself/)).toBeInTheDocument();
    expect(screen.queryByText(/add this domain to your Vercel project/)).not.toBeInTheDocument();
    unmount();
    get.mockResolvedValue({ tenant_id: "tenant-1", uploads: [], domains: [{ ...pending, edge_status: "NOT_AUTOMATED", automated: false }] });
    render(<SiteHostingCard token="t" />);
    expect(await screen.findByText(/add this domain to your Vercel project/)).toBeInTheDocument();
  });

  it("shows a setup problem and says it will be retried", async () => {
    get.mockResolvedValue({ tenant_id: "tenant-1", uploads: [], domains: [{ ...pending, edge_status: "ERROR", edge_detail: "The host refused the domain (400)." }] });
    render(<SiteHostingCard token="t" />);
    expect(await screen.findByText(/refused the domain \(400\)\. Klaros will keep trying/)).toBeInTheDocument();
  });

  it("adds a domain", async () => {
    addDomain.mockResolvedValue(pending);
    render(<SiteHostingCard token="t" />);
    fireEvent.change(await screen.findByLabelText("Domain name"), { target: { value: " shop.example.com " } });
    fireEvent.click(screen.getByRole("button", { name: "Add domain" }));
    await waitFor(() => expect(addDomain).toHaveBeenCalledWith("t", "shop.example.com"));
  });

  it("renders nothing when the screen cannot load", async () => {
    get.mockRejectedValue(new Error("down"));
    const { container } = render(<SiteHostingCard token="t" />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
