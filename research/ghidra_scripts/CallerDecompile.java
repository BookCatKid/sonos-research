// Decompile every direct caller of an address supplied as the first argument.
//@category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

public class CallerDecompile extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: CallerDecompile.java <hex-address>");
        }
        Address target = toAddr(Long.parseUnsignedLong(args[0].replaceFirst("^0x", ""), 16));
        Function callee = getFunctionAt(target);
        println("Target: " + target + " " + (callee == null ? "<no function>" : callee.getName()));

        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        for (Reference reference : getReferencesTo(target)) {
            Function caller = getFunctionContaining(reference.getFromAddress());
            println("\nReference: " + reference + " caller=" + caller);
            if (caller == null) {
                continue;
            }
            DecompileResults results = decompiler.decompileFunction(caller, 120, monitor);
            if (results.decompileCompleted()) {
                println(results.getDecompiledFunction().getC());
            } else {
                println("Decompile failed: " + results.getErrorMessage());
            }
        }
        decompiler.dispose();
    }
}
