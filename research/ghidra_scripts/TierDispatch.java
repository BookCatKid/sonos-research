// Locate the start of the pointer array (vtable) containing a given anchor
// address, then list every reference to that base and decompile the first few
// referencing functions so the dispatcher computing the AccountTier byte can
// be read.
//@category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.Symbol;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

public class TierDispatch extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs(); // [0] outfile, [1] hex anchor, [2] max-back-slots
        try (PrintWriter out = new PrintWriter(args[0], StandardCharsets.UTF_8)) {
            Address anchor = toAddr(Long.parseUnsignedLong(args[1].replaceFirst("^0x", ""), 16));
            int maxBack = Integer.parseInt(args[2]);
            out.println("anchor: " + anchor);
            // Walk back until the value stops being a function pointer.
            Address start = null;
            for (long back = 0; back <= maxBack * 8L; back += 8) {
                Address cand = anchor.subtract(back);
                long v = getLong(cand);
                Function fn = currentProgram.getFunctionManager().getFunctionAt(toAddr(v));
                if (fn == null) {
                    start = cand.add(8);
                    break;
                }
            }
            if (start == null) {
                start = anchor.subtract(maxBack * 8L);
            }
            Symbol sym = getSymbolAt(start);
            out.println("vtable base: " + start + " symbol="
                    + (sym != null ? sym.getName(true) : "<none>"));
            out.println("first slots of base:");
            for (int i = 0; i < 12; i++) {
                long v = getLong(start.add(i * 8L));
                Function fn = currentProgram.getFunctionManager().getFunctionAt(toAddr(v));
                out.println(String.format("  slot %2d @ %s = 0x%x %s", i, start.add(i * 8L), v,
                        fn != null ? fn.getName() : "?"));
            }
            Reference[] refs = getReferencesTo(start);
            out.println("references to vtable base " + start + ": " + refs.length);
            DecompInterface decompiler = new DecompInterface();
            decompiler.openProgram(currentProgram);
            try {
                int shown = 0;
                for (Reference r : refs) {
                    Function caller = getFunctionContaining(r.getFromAddress());
                    out.println("  -> " + r + " caller=" + caller);
                    if (caller != null && shown < 3) {
                        DecompileResults res = decompiler.decompileFunction(caller, 120, monitor);
                        if (res.decompileCompleted()) {
                            String code = res.getDecompiledFunction().getC();
                            if (code.length() > 9000) {
                                code = code.substring(0, 9000) + "\n...[truncated]";
                            }
                            out.println(code);
                        } else {
                            out.println("decompile failed: " + res.getErrorMessage());
                        }
                        shown += 1;
                    }
                }
            } finally {
                decompiler.dispose();
            }
        }
    }
}
