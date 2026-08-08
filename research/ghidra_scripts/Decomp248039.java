import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.address.Address;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.util.task.ConsoleTaskMonitor;
import java.io.File;
import java.io.PrintWriter;

public class Decomp248039 extends GhidraScript {
    public void run() throws Exception {
        Address ad = currentProgram.getAddressFactory().getAddress("100248039");
        Function f = currentProgram.getFunctionManager().getFunctionContaining(ad);
        if (f == null) { println("NO FUNC"); return; }
        println("func: " + f.getName() + " @ " + f.getEntryPoint());
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        DecompileResults r = ifc.decompileFunction(f, 60, new ConsoleTaskMonitor());
        File out = new File("/tmp/decomp-100248039.txt");
        PrintWriter pw = new PrintWriter(out);
        pw.println("=== " + f.getEntryPoint() + " " + f.getName() + " ===");
        pw.println(r.getDecompiledFunction().getC());
        pw.close();
        println("wrote /tmp/decomp-100248039.txt");
    }
}
