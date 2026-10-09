#include "IntermediateRepresentation/IrBuilder.hpp"
#include "Optimization/ConstantFolder.hpp"
#include <bit>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <stdexcept>

using namespace ShaderRecompiler;

namespace {

void Require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct Program {
    IrProgram ir;
    IrBuilder builder{ir};
    IrBlock& entry = Block();

    Program() {
        ir.SetEntryBlock(entry);
        builder.SetInsertionPoint(entry);
        MemoryInfo memory;
        memory.kind = ResourceKind::Lds;
        ir.Resources().memoryInfo.push_back(memory);
    }

    IrBlock& Block() {
        auto& block = ir.CreateBlock();
        ir.BlockOrder().push_back(&block);
        BlockInfo info;
        info.id = block.Id();
        ir.Metadata().blockInfo.push_back(info);
        return block;
    }

    void Split(IrBlock& left, IrBlock& right) {
        auto& info = ir.Metadata().blockInfo.at(entry.Id());
        info.condition = &Condition();
        info.terminator.kind = TerminatorKind::ConditionalBranch;
        info.terminator.trueBlock = left.Id();
        info.terminator.falseBlock = right.Id();
        entry.AddBranch(&left);
        entry.AddBranch(&right);
    }

    void Branch(IrBlock& from, IrBlock& to) {
        auto& info = ir.Metadata().blockInfo.at(from.Id());
        info.terminator.kind = TerminatorKind::Branch;
        info.terminator.trueBlock = to.Id();
        from.AddBranch(&to);
    }

    void Fold() {
        ValidateProgram(ir, true);
        ConstantFolder{}.Fold(ir);
        ValidateProgram(ir, true);
    }

    IrValue& Condition() {
        auto& lane = builder.Emit(IrOpcode::LaneId, IrType::U32, {});
        return builder.IEqual(lane, builder.Constant(0u));
    }

    IrValue& Phi(IrBlock& block, IrBlock& leftBlock, IrValue& left, IrBlock& rightBlock, IrValue& right) {
        auto& phi = ir.CreateValue(IrOpcode::Phi, left.Type());
        phi.AddPhiOperand(&leftBlock, &left);
        phi.AddPhiOperand(&rightBlock, &right);
        block.AppendInstruction(&phi);
        return phi;
    }
};

void SelectAcrossMemoryWrite() {
    Program p;
    auto& b = p.builder;
    auto& address = b.Constant(0u);
    auto& active = b.ConstantBool(true);
    auto& condition = p.Condition();
    static_cast<void>(b.Emit(IrOpcode::WriteSharedU32, IrType::Void, {&address, &b.Constant(7u), &active}));
    auto& before = b.Emit(IrOpcode::LoadSharedU32, IrType::U32, {&address, &active});
    static_cast<void>(b.Emit(IrOpcode::WriteSharedU32, IrType::Void, {&address, &b.Constant(19u), &active}));
    auto& after = b.Emit(IrOpcode::LoadSharedU32, IrType::U32, {&address, &active});
    auto& selected = b.Select(condition, before, after);
    auto& output = b.Emit(IrOpcode::ReferenceU32, IrType::Void, {&selected});
    p.Fold();
    Require(output.Argument(0)->Resolve() == &selected, "select conflated loads separated by a shared-memory write");
    Require(selected.Argument(1)->Resolve() == &before && selected.Argument(2)->Resolve() == &after, "select lost a distinct memory value");
}

void PhiAcrossBranches(IrOpcode opcode) {
    Program p;
    auto& b = p.builder;
    auto& leftBlock = p.Block();
    auto& rightBlock = p.Block();
    auto& merge = p.Block();
    auto& address = b.Constant(0u);
    auto& active = b.ConstantBool(true);
    auto& lane = b.Emit(IrOpcode::LaneId, IrType::U32, {});
    auto& addend = b.Constant(1u);
    p.Split(leftBlock, rightBlock);
    const auto value = [&](IrBlock& block, std::uint32_t word) -> IrValue& {
        b.SetInsertionPoint(block);
        if (opcode == IrOpcode::LoadSharedU32) {
            static_cast<void>(b.Emit(IrOpcode::WriteSharedU32, IrType::Void, {&address, &b.Constant(word), &active}));
            return b.Emit(opcode, IrType::U32, {&address, &active});
        }
        if (opcode == IrOpcode::IAdd32) return b.IAdd(lane, addend);
        return b.Emit(opcode, IrType::U64, {});
    };
    auto& left = value(leftBlock, 7u);
    p.Branch(leftBlock, merge);
    auto& right = value(rightBlock, 19u);
    p.Branch(rightBlock, merge);
    auto& phi = p.Phi(merge, leftBlock, left, rightBlock, right);
    b.SetInsertionPoint(merge);
    auto& zero = phi.Type() == IrType::U32 ? b.Constant(0u) : b.ConstantU64(0u);
    auto& use = b.Emit(phi.Type() == IrType::U32 ? IrOpcode::IEqual32 : IrOpcode::IEqual64, IrType::U1, {&phi, &zero});
    p.Fold();
    Require(phi.Resolve() == &phi, "phi replaced branch-local values with a non-dominating instruction");
    Require(use.Argument(0)->Resolve() == &phi, "phi use lost its predecessor-dependent value");
}

void SelectConstants(std::uint32_t leftBits, std::uint32_t rightBits, bool folds) {
    Program p;
    auto& b = p.builder;
    auto& left = b.ConstantF32(std::bit_cast<float>(leftBits));
    auto& right = b.ConstantF32(std::bit_cast<float>(rightBits));
    auto& selected = b.Select(p.Condition(), left, right);
    p.Fold();
    Require((selected.Resolve() == &left) == folds, "select changed bitwise floating-point constant equality");
    Require(folds || selected.Resolve() == &selected, "distinct floating-point constants were folded");
}

void SafeSelectFolds() {
    Program p;
    auto& b = p.builder;
    auto& lane = b.Emit(IrOpcode::LaneId, IrType::U32, {});
    auto& same = b.Select(p.Condition(), lane, lane);
    auto& yes = b.Select(b.ConstantBool(true), lane, b.Constant(7u));
    auto& no = b.Select(b.ConstantBool(false), b.Constant(7u), lane);
    p.Fold();
    Require(same.Resolve() == &lane, "select of one SSA value stopped folding");
    Require(yes.Resolve() == &lane && no.Resolve() == &lane, "constant-condition select stopped folding");
}

void SafePhiFolds(bool constants) {
    Program p;
    auto& b = p.builder;
    auto& leftBlock = p.Block();
    auto& rightBlock = p.Block();
    auto& merge = p.Block();
    auto& left = constants ? b.Constant(7u) : b.Emit(IrOpcode::LaneId, IrType::U32, {});
    auto& right = constants ? b.Constant(7u) : left;
    p.Split(leftBlock, rightBlock);
    p.Branch(leftBlock, merge);
    p.Branch(rightBlock, merge);
    auto& phi = p.Phi(merge, leftBlock, left, rightBlock, right);
    p.Fold();
    Require(phi.Resolve() == &left, "phi of equal constants or one SSA value stopped folding");
}

}

int main() {
    int cases = 0;
    int failures = 0;
    const auto run = [&](const char* name, auto test) {
        ++cases;
        try {
            test();
        } catch (const std::exception& error) {
            std::fprintf(stderr, "FAIL: %s: %s\n", name, error.what());
            ++failures;
        }
    };
    run("select across memory write", SelectAcrossMemoryWrite);
    run("phi of branch-local loads", [] { PhiAcrossBranches(IrOpcode::LoadSharedU32); });
    run("phi of branch-local additions", [] { PhiAcrossBranches(IrOpcode::IAdd32); });
    run("phi of shader clocks", [] { PhiAcrossBranches(IrOpcode::ShaderClock); });
    run("phi of realtime clocks", [] { PhiAcrossBranches(IrOpcode::RealtimeClock); });
    run("distinct signed zeros", [] { SelectConstants(0u, 0x80000000u, false); });
    run("distinct NaN payloads", [] { SelectConstants(0x7fc00001u, 0x7fc00002u, false); });
    run("equal NaN payloads", [] { SelectConstants(0x7fc00001u, 0x7fc00001u, true); });
    run("equal float constants", [] { SelectConstants(0x3f800000u, 0x3f800000u, true); });
    run("safe select folds", SafeSelectFolds);
    run("phi of one SSA value", [] { SafePhiFolds(false); });
    run("phi of equal constants", [] { SafePhiFolds(true); });
    std::printf("constant folding: %d cases, %d failures\n", cases, failures);
    return failures == 0 ? 0 : 1;
}
