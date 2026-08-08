// Dump a vtable (pointer table) to a file, showing each slot's target function name.
// @category Sonos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.symbol.Symbol;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

public class VtableDump extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 3) throw new IllegalArgumentException("output file, vtable hex, slot count");
        Address vtable = toAddr(Long.parseUnsignedLong(args[1].replaceFirst("^0x", ""), 16));
        int slots = Integer.decode(args[2]);
        try (PrintWriter out = new PrintWriter(args[0], StandardCharsets.UTF_8)) {
            for (int i = 0; i < slots; i++) {
                Address slotAddr = vtable.add(i * 8L);
                long value = getLong(slotAddr);
                Address target = toAddr(value);
                Symbol symbol = getSymbolAt(target);
                out.println("[" + i + "] " + slotAddr + " -> " + target + " " +
                        (symbol == null ? "<unknown>" : symbol.getName(true)));
            }
        }
    }
}
