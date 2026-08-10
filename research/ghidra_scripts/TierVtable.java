// Given a data reference to a method (a vtable slot), find the vtable array
// that contains it, identify nearby slots (class fingerprint), and resolve
// references to the vtable base so the calling code can be decompiled.
//@category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolTable;

public class TierVtable extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try {
            for (String hex : args) {
                Address slot = toAddr(Long.parseUnsignedLong(hex.replaceFirst("^0x", ""), 16));
                println("================================");
                println("vtable slot containing 0x" + hex);
                // Find the nearest preceding symbol (vtable base candidate).
                Symbol base = null;
                SymbolTable table = currentProgram.getSymbolTable();
                Symbol[] syms = table.getSymbols(slot);
                if (syms.length == 0) {
                    for (long back = 0x8; back < 0x400; back += 0x8) {
                        syms = table.getSymbols(slot.subtract(back));
                        if (syms.length > 0) {
                            base = syms[0];
                            slot = slot.subtract(back);
                            break;
                        }
                    }
                } else {
                    base = syms[0];
                }
                println("vtable base symbol: " + (base != null ? base.getName() : "?") + " @ " + slot);
                // Dump the first 24 slots to fingerprint the class.
                for (int i = 0; i < 24; i++) {
                    Address entry = slot.add(i * 8);
                    long target = currentProgram.getMemory().getLong(entry);
                    Function fn = currentProgram.getFunctionManager()
                            .getFunctionAt(toAddr(Long.toHexString(target)));
                    println(String.format("  slot %2d @ %s -> 0x%x %s",
                            i, entry, target, fn != null ? fn.getName() : "?"));
                }
                // References to the vtable base.
                Reference[] brefs = getReferencesTo(slot);
                println("references to vtable base " + slot + ": " + brefs.length);
                int shown = 0;
                for (Reference bref : brefs) {
                    Function caller = getFunctionContaining(bref.getFromAddress());
                    println("  -> " + bref + " caller=" + caller);
                    if (caller != null && shown < 3) {
                        DecompileResults res = decompiler.decompileFunction(caller, 120, monitor);
                        if (res.decompileCompleted()) {
                            String code = res.getDecompiledFunction().getC();
                            if (code.length() > 8000) {
                                code = code.substring(0, 8000) + "\n...[truncated]";
                            }
                            println(code);
                        } else {
                            println("decompile failed: " + res.getErrorMessage());
                        }
                        shown += 1;
                    }
                }
            }
        } finally {
            decompiler.dispose();
        }
    }
}
