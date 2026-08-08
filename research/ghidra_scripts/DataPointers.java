// Print pointer-sized values and primary symbols over an address range.
//@category Sonos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.symbol.Symbol;

public class DataPointers extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2) throw new IllegalArgumentException("start hex and byte count required");
        Address address = toAddr(Long.parseUnsignedLong(args[0].replaceFirst("^0x", ""), 16));
        int count = Integer.decode(args[1]);
        for (int offset = 0; offset < count; offset += 8) {
            Address current = address.add(offset);
            long value = getLong(current);
            Address target = toAddr(value);
            Symbol symbol = getSymbolAt(target);
            println(current + " -> " + target + (symbol == null ? "" : " " + symbol.getName(true)));
        }
    }
}
