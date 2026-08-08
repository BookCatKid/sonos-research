import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.symbol.*;

public class FindModule extends GhidraScript {
    public void run() throws Exception {
        String[] targets = {"SCContentSessionBrowse", "SCContentSession", "Refreshing token", "refreshTokenFor", "onInvalidToken", "InvalidToken", "HTTPbrowseFailed"};
        for (String s : targets) {
            Address addr = null;
            var iter = currentProgram.getListing().getDefinedData(true);
            while (iter.hasNext()) {
                var d = iter.next();
                if (d.hasStringValue() && s.equals(d.getValue().toString())) { addr = d.getAddress(); break; }
            }
            if (addr == null) { println("NOTFOUND: " + s); continue; }
            println("'" + s + "' at " + addr);
            int n=0;
            for (Reference r : currentProgram.getReferenceManager().getReferencesTo(addr)) {
                Address from = r.getFromAddress();
                Function f = currentProgram.getFunctionManager().getFunctionContaining(from);
                println("  xref " + from + " in " + (f!=null? f.getName()+"@"+f.getEntryPoint() : "?"));
                if (++n>15) break;
            }
        }
    }
}
