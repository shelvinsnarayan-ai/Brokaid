/** R3.12 — plain explainers (general information only; must pass the advice guard) and "What happens next" steps. One file. */
export const EXPLAIN: Record<string, { title: string; body: string }> = {
  excess: { title: "Excess", body: "The excess is the amount you pay towards a claim before the insurer pays the rest. It's set out on your schedule. Some claims can have more than one excess." },
  sum_insured: { title: "Sum insured", body: "The sum insured is the most the insurer will pay for that item or section. It's the figure on your schedule. If it's lower than the real replacement cost, a claim may not be paid in full." },
  agreed_value: { title: "Agreed value", body: "Agreed value is an amount you and the insurer settle on when the policy starts. If the vehicle is written off, that's the amount used, less any excess." },
  market_value: { title: "Market value", body: "Market value is what the vehicle is worth on the day of the loss, based on age, kilometres and condition. It can go up or down over the year." },
  limit_of_liability: { title: "Limit of liability", body: "The limit of liability is the most the insurer will pay for one claim, or for all claims in the period if there's an aggregate. Legal costs may be inside or on top of the limit; the wording says which." },
  premium: { title: "Premium", body: "The premium is what the policy costs for the period of cover. The amount shown includes GST and stamp duty. Your broker's fees, if any, are listed separately on the invoice." },
  gst_stamp_duty: { title: "GST and stamp duty", body: "GST is the 10% goods and services tax. Stamp duty is a state government charge on insurance, and the rate depends on the state and type of cover. Both are included in the premium you pay." },
  renewal_date: { title: "Renewal date", body: "This is the last day of your current cover. Your broker starts the renewal ahead of this date. Cover stops on this date unless the renewal is finished and paid." },
};

export const NEXT: Record<string, string[]> = {
  change: ["Your broker reads your request and replies by the next business day.",
    "If the insurer needs to agree, your broker asks them and tells you any cost before anything changes.",
    "You accept the change here, then your updated documents appear in Documents."],
  claim: ["Your broker acknowledges your claim within 1 business day.",
    "Your broker lodges it with the insurer. The insurer may ask for photos, quotes or other documents.",
    "You can follow every step and message your broker from the claim page."],
  pay: ["Pay by BPAY or bank transfer using the reference shown.",
    "Tap \"I've paid this\" so your broker knows to look for it.",
    "We confirm once the money reaches the trust account, usually within 1–2 business days."],
  certificate: ["Tell us who the certificate is for.",
    "If it's a standard certificate it's ready straight away. If it needs a broker, they reply by the next business day.",
    "Download or share the certificate from this page."],
};
