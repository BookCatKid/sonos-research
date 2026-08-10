// Find every CALL instruction whose memory operand carries a displacement of
// 0xc0 or 0xc8 (vtable slots 24/25 -- the two AddOAuthAccountX methods), list
// the containing function for each, then decompile each unique function so the
// tier byte computation can be read.
//@category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.scalar.Scalar;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashSet;
import java.util.Set;

public class VtableCallScan extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs(); // [0] outfile, [1..] hex displacements
        long[] wanted = new long[args.length - 1];
        for (int i = 1; i < args.length; i++) {
            wanted[i - 1] = Long.parseUnsignedLong(args[i].replaceFirst("^0x", ""), 16);
        }
        Set<Function> hits = new LinkedHashSet<>();
        try (PrintWriter out = new PrintWriter(args[0], StandardCharsets.UTF_8)) {
            InstructionIterator it = currentProgram.getListing().getInstructions(true);
            long scanned = 0;
            while (it.hasNext()) {
                Instruction ins = it.next();
                scanned += 1;
                if (!"CALL".equals(ins.getMnemonicString())) {
                    continue;
                }
                Object[] objs = ins.getOpObjects(0);
                for (Object o : objs) {
                    if (o instanceof Scalar) {
                        long v = ((Scalar) o).getValue();
                        for (long w : wanted) {
                            if (v == w) {
                                Function fn = getFunctionContaining(ins.getAddress());
                                hits.add(fn);
                                out.println("hit @" + ins.getAddress() + " disp=0x" + Long.toHexString(v)
                                        + " insn=" + ins + " inFunc=" + fn);
                            }
                        }
                    }
                }
            }
            out.println("scanned instructions: " + scanned);
            out.println("unique functions dispatching 0xc0/0xc8: " + hits.size());
            DecompInterface decompiler = new DecompInterface();
            decompiler.openProgram(currentProgram);
            try {
                for (Function fn : hits) {
                    out.println("==========================================");
                    out.println("FUNCTION " + fn.getName() + " @ " + fn.getEntryPoint());
                    DecompileResults res = decompiler.decompileFunction(fn, 120, monitor);
                    if (res.decompileCompleted()) {
                        String code = res.getDecompiledFunction().getC();
                        if (code.length() > 10000) {
                            code = code.substring(0, 10000) + "\n...[truncated]";
                        }
                        out.println(code);
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
