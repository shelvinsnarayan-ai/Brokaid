import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { MockedBadge } from "../MockedBadge";

describe("<MockedBadge />", () => {
  it("renders the label in uppercase with the MOCKED prefix", () => {
    const { getByTestId } = render(<MockedBadge label="carrier" />);
    const el = getByTestId("mocked-badge-carrier");
    expect(el.textContent).toBe("MOCKED · CARRIER");
  });

  it("exposes an accessible label", () => {
    const { getByRole } = render(<MockedBadge label="payment" />);
    const el = getByRole("status");
    expect(el.getAttribute("aria-label")).toBe(
      "This action is mocked for demo purposes"
    );
  });

  it("operator variant uses the operator class", () => {
    const { getByTestId } = render(<MockedBadge label="e-sign" variant="operator" />);
    const el = getByTestId("mocked-badge-e-sign");
    expect(el.className).toContain("brokaid-mocked--operator");
    expect(el.textContent).toBe("MOCKED · E-SIGN");
    expect(el.tagName).toBe("SPAN");
  });

  it("client variant uses the client class", () => {
    const { getByTestId } = render(<MockedBadge label="insurer" variant="client" />);
    const el = getByTestId("mocked-badge-insurer");
    expect(el.className).toContain("brokaid-mocked--client");
    expect(el.textContent).toBe("MOCKED · INSURER");
  });

  it("supports all documented labels", () => {
    const labels = ["carrier", "payment", "e-sign", "insurer", "ams", "funder", "signal"] as const;
    for (const l of labels) {
      const { getByTestId } = render(<MockedBadge label={l} />);
      expect(getByTestId(`mocked-badge-${l}`).textContent).toContain("MOCKED");
    }
  });
});
