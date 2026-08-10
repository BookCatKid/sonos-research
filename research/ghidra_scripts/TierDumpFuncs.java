// Decompile the given functions in full and write each body to an output file.
//@category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

public class TierDumpFuncs extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs(); // [0] outfile, [1..] hex function addresses
        try (PrintWriter out = new PrintWriter(args[0], StandardCharsets.UTF_8)) {
            DecompInterface decompiler = new DecompInterface();
            decompiler.openProgram(currentProgram);
            try {
                for (int i = 1; i < args.length; i++) {
                    String hex = args[i];
                    Function fn = getFunctionContaining(toAddr(Long.parseUnsignedLong(
                            hex.replaceFirst("^0x", ""), 16)));
                    out.println("==================================================");
                    out.println("FUNCTION " + (fn != null ? fn.getName() : "?") + " @ 0x" + hex
                            + (fn != null ? " (entry " + fn.getEntryPoint() + ")" : ""));
                    if (fn == null) {
                        continue;
                    }
                    DecompileResults res = decompiler.decompileFunction(fn, 180, monitor);
                    if (res.decompileCompleted()) {
                        out.println(res.getDecompiledFunction().getC());
                    } else {
                        out.println("decompile failed: " + res.getErrorMessage());
                    }
                }
            } finally {
                decompiler.dispose();
            }
        }
    }
}
