// Decompile functions that reference Sonos music-service/account protocol markers.
// @category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.data.DataType;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public class SonosKeyXrefs extends GhidraScript {
    private static final List<String> STRING_MARKERS = Arrays.asList(
        "HiddenPreloadSvcs",
        "ListAvailableServices",
        "ServiceListVersion",
        "ThirdPartyMediaServers",
        "ThirdPartyMediaServersX",
        "ThirdPartyHash",
        "CustomerID",
        "AccountUDN",
        "AccountNickname",
        "AccountType",
        "AvailableServiceDescriptorList",
        "AvailableServiceTypeList",
        "AvailableServiceListVersion",
        "SA_RINCON",
        "OnServiceListChanged",
        "GetString Failed"
    );

    private static final List<String> SYMBOL_MARKERS = Arrays.asList(
        "RUpnpSPGetStringAIOOp",
        "RListAvailableServicesWithGetStringCompoundOp",
        "RListAvailableServicesWithMSDLogoParserAIOOp",
        "RUpnpMSDListAvailableServicesAIOOp",
        "RUpnpMSDUpdateAvailableServicesAIOOp",
        "RSvcAccountsCB",
        "SCServiceAccountManager",
        "SCServiceDescriptorManager",
        "SCLANHouseholdAdapter",
        "RAccountsVectorClock"
    );

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) {
            throw new IllegalArgumentException("Expected one output-file argument");
        }

        Listing listing = currentProgram.getListing();
        Map<Function, Set<String>> functions = new LinkedHashMap<>();

        SymbolIterator symbols = currentProgram.getSymbolTable().getAllSymbols(true);
        while (symbols.hasNext() && !monitor.isCancelled()) {
            Symbol symbol = symbols.next();
            String name = symbol.getName(true);
            for (String marker : SYMBOL_MARKERS) {
                if (name.contains(marker)) {
                    collectReferences(listing, symbol.getAddress(), "symbol:" + marker, functions);
                }
            }
        }

        DataIterator dataIterator = listing.getDefinedData(true);
        while (dataIterator.hasNext() && !monitor.isCancelled()) {
            Data data = dataIterator.next();
            Object value = data.getValue();
            if (!(value instanceof String)) {
                continue;
            }
            String text = (String) value;
            for (String marker : STRING_MARKERS) {
                if (text.contains(marker)) {
                    collectReferences(listing, data.getAddress(), "string:" + marker, functions);
                }
            }
        }

        DecompInterface decompiler = new DecompInterface();
        decompiler.toggleCCode(true);
        decompiler.toggleSyntaxTree(true);
        decompiler.openProgram(currentProgram);

        try (PrintWriter output = new PrintWriter(args[0], StandardCharsets.UTF_8)) {
            output.println("Program: " + currentProgram.getName());
            output.println("Matched functions: " + functions.size());
            output.println();

            int index = 0;
            for (Map.Entry<Function, Set<String>> entry : functions.entrySet()) {
                if (monitor.isCancelled() || index++ >= 120) {
                    break;
                }
                Function function = entry.getKey();
                output.println("================================================================================");
                output.println("Function: " + function.getName(true));
                output.println("Entry: " + function.getEntryPoint());
                output.println("Markers: " + String.join(", ", entry.getValue()));

                DecompileResults results = decompiler.decompileFunction(function, 90, monitor);
                if (results.decompileCompleted() && results.getDecompiledFunction() != null) {
                    output.println(results.getDecompiledFunction().getC());
                } else {
                    output.println("DECOMPILE FAILED: " + results.getErrorMessage());
                }
            }
        } finally {
            decompiler.dispose();
        }
    }

    private void collectReferences(
        Listing listing,
        ghidra.program.model.address.Address address,
        String marker,
        Map<Function, Set<String>> functions
    ) {
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(address);
        while (references.hasNext()) {
            Reference reference = references.next();
            Function function = listing.getFunctionContaining(reference.getFromAddress());
            if (function != null) {
                functions.computeIfAbsent(function, ignored -> new LinkedHashSet<>()).add(marker);
                continue;
            }

            // RTTI symbols are normally referenced by a vtable data slot. Follow that
            // intermediate address once to find constructors and operation factories.
            ReferenceIterator secondHop = currentProgram.getReferenceManager()
                .getReferencesTo(reference.getFromAddress());
            while (secondHop.hasNext()) {
                Reference secondReference = secondHop.next();
                Function secondFunction = listing.getFunctionContaining(secondReference.getFromAddress());
                if (secondFunction != null) {
                    functions.computeIfAbsent(secondFunction, ignored -> new LinkedHashSet<>())
                        .add(marker + ":via-data");
                }
            }
        }
    }
}
