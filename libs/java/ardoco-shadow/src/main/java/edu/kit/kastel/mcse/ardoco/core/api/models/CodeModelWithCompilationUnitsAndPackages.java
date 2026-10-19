package edu.kit.kastel.mcse.ardoco.core.api.models;

import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeItem;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeItemRepository;
import java.util.ArrayList;
import java.util.List;
import java.util.SortedSet;
import java.util.TreeSet;

/**
 * Replaces the ArDoCo 2.1.0 class of the same name (this module comes first on the class path). ArDoCo calls
 * {@code getEndpoints} and {@code getTypeIdentifiers} for every word and rebuilds both from the whole code model each
 * time (58 % of an ardocode run, F-1). The model never changes after it is read, so both results are computed once
 * and returned as copies. {@code CodeModelWithCompilationUnitsAndPackagesCacheTest} fails if this class is not the one
 * that is loaded.
 */
// ponytail: copy of ArDoCo 2.1.0; raise ardoco.version only after comparing with the new original (VersionTest and the
// benchmark equivalence test guard the pipeline copy, this file needs the same check). Upgrade path: ask ArDoCo to
// cache.
public final class CodeModelWithCompilationUnitsAndPackages extends CodeModel {

    private final CodeModelWithCompilationUnits codeModel;
    private List<CodeItem> endpoints;
    private SortedSet<String> typeIdentifiers;

    public CodeModelWithCompilationUnitsAndPackages(CodeModelDto codeModelDto) {
        super(codeModelDto.id(), codeModelDto.codeItemRepository(), codeModelDto.content());
        this.codeModel = new CodeModelWithCompilationUnits(codeModelDto);
    }

    public CodeModelWithCompilationUnitsAndPackages(
            CodeItemRepository codeItemRepository, SortedSet<? extends CodeItem> content) {
        super(codeItemRepository, content);
        this.codeModel = new CodeModelWithCompilationUnits(codeItemRepository, content);
    }

    @Override
    public List<? extends CodeItem> getContent() {
        return getEndpoints();
    }

    @Override
    public synchronized List<CodeItem> getEndpoints() {
        if (endpoints == null) {
            List<CodeItem> result = new ArrayList<>(codeModel.getAllPackages());
            for (CodeItem item : codeModel.getContent()) {
                result.addAll(item.getAllCompilationUnits());
            }
            endpoints = result;
        }
        return new ArrayList<>(endpoints);
    }

    @Override
    public Metamodel getMetamodel() {
        return Metamodel.CODE_WITH_COMPILATION_UNITS_AND_PACKAGES;
    }

    @Override
    public synchronized SortedSet<String> getTypeIdentifiers() {
        if (typeIdentifiers == null) {
            SortedSet<String> result = new TreeSet<>();
            for (CodeItem item : getEndpoints()) {
                item.getType().ifPresent(result::add);
            }
            typeIdentifiers = result;
        }
        return new TreeSet<>(typeIdentifiers);
    }
}
