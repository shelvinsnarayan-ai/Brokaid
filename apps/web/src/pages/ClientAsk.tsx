/**
 * Ask tab — chat shell. Input disabled until the ask flow lands.
 */
export function ClientAsk() {
  return (
    <section className="brokaid-tab brokaid-tab--ask" data-testid="tab-ask-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Ask</p>
        <h1 className="brokaid-tab__title">Your broker line</h1>
      </header>

      <ol className="brokaid-ask" data-testid="ask-thread">
        <li className="brokaid-ask__msg brokaid-ask__msg--broker">
          <div className="brokaid-ask__meta">BurMac · your broker</div>
          <div className="brokaid-ask__body">
            Hi. This is your broker's line. What do you need?
          </div>
        </li>
      </ol>

      <form
        className="brokaid-ask__form"
        onSubmit={(e) => e.preventDefault()}
        aria-label="Send a message to your broker"
      >
        <input
          type="text"
          placeholder="The send button lights up when the Ask flow ships…"
          disabled
          data-testid="ask-input"
        />
        <button
          type="submit"
          disabled
          className="brokaid-btn brokaid-btn--primary"
          data-testid="ask-send"
        >
          Send
        </button>
      </form>
      <p className="brokaid-tab__note">
        The messaging flow lands in a later phase. Tap Home for policies.
      </p>
    </section>
  );
}
