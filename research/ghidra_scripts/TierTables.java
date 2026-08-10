// Dump the pointer-table structure around given slot addresses, the symbols at
// each slot, and every reference to each slot (to find the dispatcher that
// invokes the AddOAuthAccountX wrappers and computes the tier byte).
//@category Sonos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.listing.Function;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

public class TierTables extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs(); // [0] = outfile, [1..] = hex slot addresses
        try (PrintWriter out = new PrintWriter(args[0], StandardCharsets.UTF_8)) {
            for (int a = 1; a < args.length; a++) {
                String hex = args[a];
                Address slot = toAddr(Long.parseUnsignedLong(hex.replaceFirst("^0x", ""), 16));
                out.println("==================================================");
                out.println("slot @ 0x" + hex);
                Symbol sym = getSymbolAt(slot);
                out.println("symbol at slot: " + (sym != null ? sym.getName(true) : "<none>"));
                Reference[] refs = getReferencesTo(slot);
                out.println("refs to slot: " + refs.length);
                for (Reference r : refs) {
                    Function f = getFunctionContaining(r.getFromAddress());
                    out.println("  ref from " + r.getFromAddress() + " type=" + r.getReferenceType()
                            + " inFunc=" + f);
                }
                for (int i = -8; i <= 16; i++) {
                    Address addr = slot.add(i * 8L);
                    long v = getLong(addr);
                    Symbol ts = getSymbolAt(toAddr(v));
                    Function tf = currentProgram.getFunctionManager()
                            .getFunctionAt(toAddr(v));
                    out.println(String.format("  %+3d @ %s = 0x%x %s", i, addr, v,
                            tf != null ? "FUN " + tf.getName()
                                    : (ts != null ? ts.getName(true) : "")));
                }
            }
        }
    }
}
