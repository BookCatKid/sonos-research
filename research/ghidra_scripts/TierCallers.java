// Decompile every caller of a list of functions (the AddOAuthAccountX
// account-object constructor/destructor) and print the raw C so the vtable
// dispatch and its arguments (especially AccountTier) can be inspected.
//@category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

public class TierCallers extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try {
            for (String hex : args) {
                Address target = toAddr(Long.parseUnsignedLong(hex.replaceFirst("^0x", ""), 16));
                println("================================");
                println("xrefs to 0x" + hex + ": " + getReferencesTo(target).length);
                int shown = 0;
                for (Reference ref : getReferencesTo(target)) {
                    Function caller = getFunctionContaining(ref.getFromAddress());
                    println("-- " + ref + " caller=" + caller);
                    if (caller == null) {
                        continue;
                    }
                    DecompileResults res = decompiler.decompileFunction(caller, 120, monitor);
                    if (res.decompileCompleted()) {
                        String code = res.getDecompiledFunction().getC();
                        if (code.length() > 12000) {
                            code = code.substring(0, 12000) + "\n...[truncated]";
                        }
                        println(code);
                        shown += 1;
                        if (shown >= 4) {
                            break;
                        }
                    } else {
                        println("decompile failed: " + res.getErrorMessage());
                    }
                }
            }
        } finally {
            decompiler.dispose();
        }
    }
}
