// Trace references to a target function and decompile every caller to see
// how its arguments are computed.  For data references (vtable slots) the
// containing block is treated as a vtable and references to the block start
// are resolved to their calling functions too.
//@category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;

public class TierTrace extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String targetHex = args.length > 0 ? args[0] : "100e609a0";
        Address target = toAddr(Long.parseUnsignedLong(targetHex.replaceFirst("^0x", ""), 16));
        println("=== references to 0x" + targetHex + " ===");

        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try {
            for (Reference ref : getReferencesTo(target)) {
                Address from = ref.getFromAddress();
                Function inFunc = getFunctionContaining(from);
                println("\n--- ref from " + from + " type=" + ref.getReferenceType()
                        + " inFunc=" + inFunc + " ---");
                if (inFunc != null) {
                    dump(decompiler, inFunc);
                } else {
                    MemoryBlock block = getMemoryBlock(from);
                    println("data ref in block: " + (block != null ? block.getName()
                            + " start=" + block.getStart() : "?"));
                    if (block != null) {
                        Reference[] brefs = getReferencesTo(block.getStart());
                        println("references to block start " + block.getStart() + ": " + brefs.length);
                        int shown = 0;
                        for (Reference bref : brefs) {
                            Function bcaller = getFunctionContaining(bref.getFromAddress());
                            println("  -> " + bref + " caller=" + bcaller);
                            if (bcaller != null && shown < 4) {
                                dump(decompiler, bcaller);
                                shown += 1;
                            }
                        }
                    }
                }
            }
        } finally {
            decompiler.dispose();
        }
    }

    private void dump(DecompInterface decompiler, Function fn) {
        DecompileResults res = decompiler.decompileFunction(fn, 120, monitor);
        if (res.decompileCompleted()) {
            String code = res.getDecompiledFunction().getC();
            if (code.length() > 9000) {
                code = code.substring(0, 9000) + "\n...[truncated]";
            }
            println(code);
        } else {
            println("decompile failed: " + res.getErrorMessage());
        }
    }
}
