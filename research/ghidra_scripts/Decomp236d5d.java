import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.address.Address;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.util.task.ConsoleTaskMonitor;
import java.io.File;
import java.io.PrintWriter;

public class Decomp236d5d extends GhidraScript {
    public void run() throws Exception {
        String[] addrs = {"1001d89d0","100236d5d"};
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        for (String a : addrs) {
            Address ad = currentProgram.getAddressFactory().getAddress(a);
            Function f = currentProgram.getFunctionManager().getFunctionContaining(ad);
            if (f == null) { println("NO FUNC " + a); continue; }
            DecompileResults r = ifc.decompileFunction(f, 60, new ConsoleTaskMonitor());
            File out = new File("/tmp/decomp-" + a + ".txt");
            PrintWriter pw = new PrintWriter(out);
            pw.println("=== " + f.getEntryPoint() + " " + f.getName() + " ===");
            pw.println(r.getDecompiledFunction().getC());
            pw.close();
            println("wrote /tmp/decomp-" + a + ".txt " + f.getEntryPoint());
        }
    }
}
