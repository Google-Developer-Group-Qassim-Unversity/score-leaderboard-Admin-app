export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const dns = await import('dns');
    const net = await import('net');
    // The VPS has no outbound IPv6 route: IPv4 connects in ~350ms, IPv6
    // fails instantly with ENETUNREACH (confirmed directly on the box).
    // dns.setDefaultResultOrder alone does NOT fix this - Node's
    // autoSelectFamily (Happy Eyeballs, on by default since Node 19+)
    // races both families itself and ignores the lookup order, and its
    // racer has a known bug (nodejs/node#54359) that cancels the working
    // IPv4 attempt, producing a consistent ~250ms ETIMEDOUT on every call
    // - verified with 8/8 failures before this fix, 8/8 successes after.
    // Disabling autoSelectFamily is required so Node falls back to a
    // single dns.lookup(), which then honors ipv4first.
    dns.setDefaultResultOrder('ipv4first');
    net.setDefaultAutoSelectFamily(false);
  }
}
