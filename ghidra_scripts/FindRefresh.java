import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.symbol.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.data.*;

public class FindRefresh extends GhidraScript {
    public void run() throws Exception {
        String[] targets = {"Refreshing token for UDN", "refreshAuthToken", "X-Sonos-SMAPI-Auth", "getMetadata", "getExtendedMetadata"};
        for (String s : targets) {
            Address addr = findString(s);
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
    private Address findString(String s) {
        var iter = currentProgram.getListing().getDefinedData(true);
        while (iter.hasNext()) {
            var d = iter.next();
            if (d.hasStringValue()) {
                String v = d.getValue().toString();
                if (s.equals(v)) return d.getAddress();
            }
        }
        return null;
    }
}
