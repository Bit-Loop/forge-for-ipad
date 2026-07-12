// Forge-authored StikDebug adapter for UTM's public BRK 0x69 handshake.
function readLE64(hex) {
  const bytes = hex.match(/../g).map(x => BigInt(parseInt(x, 16)));
  return bytes.reduceRight((n, b) => (n << 8n) | b, 0n);
}
function writeLE64(value) {
  const bytes = [];
  for (let i = 0; i < 8; i++, value >>= 8n) bytes.push(Number(value & 255n));
  return bytes.map(x => x.toString(16).padStart(2, "0")).join("");
}
const pid = get_pid();
send_command(`vAttach;${pid.toString(16)}`);
while (true) {
  const stop = send_command("c");
  const thread = /thread:([0-9a-f]+);/.exec(stop)?.[1];
  const pcHex = /20:([0-9a-f]{16});/.exec(stop)?.[1];
  const addressHex = /00:([0-9a-f]{16});/.exec(stop)?.[1];
  const lengthHex = /01:([0-9a-f]{16});/.exec(stop)?.[1];
  if (!thread || !pcHex || !addressHex || !lengthHex) continue;
  const pc = readLE64(pcHex);
  const instruction = parseInt(send_command(`m${pc.toString(16)},4`).match(/../g).reverse().join(""), 16);
  if (((instruction >>> 5) & 0xffff) !== 0x69) continue;
  prepare_memory_region(readLE64(addressHex), readLE64(lengthHex));
  send_command(`P20=${writeLE64(pc + 4n)};thread:${thread};`);
  break;
}
send_command("D");
