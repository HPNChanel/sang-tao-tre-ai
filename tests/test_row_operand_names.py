"""Row-name evidence must come from the question, independently of the plan."""
import itertools

import openpyxl
import pytest

from src import plan_review
from src.xlsx_tables import load_tables


def table(tmp_path, labels):
    book = openpyxl.Workbook()
    book.active.append(["Group", "Score"])
    for n, label in enumerate(labels):
        book.active.append([label, n + 1])
    path = tmp_path / "groups.xlsx"
    book.save(path)
    return load_tables(path)


def ranking(paths, names=None):
    return {"version": 1, "table": {"index": 0}, "op": "rank",
            "rows": {"paths": paths}, "columns": ["Score"],
            "args": {"by": "Score", "names": names or paths},
            "format": {"kind": "list", "item_kind": "text"}}


@pytest.mark.parametrize("labels", list(itertools.permutations(
    ["Redwood and related", "Birch", "Elm"])))
@pytest.mark.parametrize("names", ["Redwood, Birch, Elm", "Elm, Redwood and Birch",
                                  "Redwood and related, Birch, and Elm"])
def test_unique_whole_word_alias_is_independent_of_row_order(tmp_path, labels, names):
    tables = table(tmp_path, labels)
    query = "Rank by Score: " + names + ". Answer with the three names."
    assert not plan_review.semantic_errors(ranking(list(labels)), query, tables)


@pytest.mark.parametrize("extra", ["Redwood services", "Redwood and related"])
def test_ambiguous_prefix_never_proves_a_row_identity(tmp_path, extra):
    tables = table(tmp_path, ["Redwood and related", extra, "Birch"])
    query = "Rank by Score: Redwood and Birch."
    errors = plan_review.semantic_errors(ranking(["Redwood and related", "Birch"]), query, tables)
    assert "explicit row name is ambiguous" in errors


def test_exact_name_takes_precedence_over_prefix(tmp_path):
    tables = table(tmp_path, ["Redwood", "Redwood and related", "Birch"])
    query = "Rank by Score: Redwood and Birch."
    assert not plan_review.semantic_errors(ranking(["Redwood", "Birch"]), query, tables)
    assert plan_review.semantic_errors(ranking(["Redwood and related", "Birch"]), query, tables)


@pytest.mark.parametrize("query", ["Rank by Score: Redwood and Birch.",
                                  "Rank by Score: Red and Birch.",
                                  "Rank Birch by Score. Answer with names: Redwood, Birch."])
def test_wrong_row_cannot_be_justified_by_plan_names_or_format_text(tmp_path, query):
    tables = table(tmp_path, ["Redwood and related", "Birch", "Elm"])
    bad = ranking(["Elm", "Birch"], names=["Redwood", "Birch"])
    assert plan_review.semantic_errors(bad, query, tables)


def test_partial_word_is_not_a_row_alias(tmp_path):
    tables = table(tmp_path, ["Redwood and related", "Birch"])
    assert plan_review.semantic_errors(ranking(["Redwood and related", "Birch"]),
                                       "Rank by Score: Red and Birch.", tables)


def test_alias_in_exclusion_does_not_authorize_selected_row(tmp_path):
    tables = table(tmp_path, ["Redwood and related", "Birch", "Elm"])
    query = "Rank by Score: Birch, Elm, excluding Redwood."
    assert plan_review.semantic_errors(ranking(["Redwood and related", "Birch"]), query, tables)


def test_duplicate_leaf_requires_an_explicit_parent(tmp_path, monkeypatch):
    tables = table(tmp_path, ["Redwood and related", "Redwood and related", "Birch"])
    view = plan_review._view_for(ranking([]), tables)
    first, second = view.df.index[:2]
    view.df.loc[first, "_row_path"] = "North > Redwood and related"
    view.df.loc[second, "_row_path"] = "South > Redwood and related"
    monkeypatch.setattr(plan_review, "_view_for", lambda *args: view)
    query = "Rank by Score: North > Redwood and Birch."
    assert not plan_review.semantic_errors(ranking(["North > Redwood and related", "Birch"]), query, tables)
    assert plan_review.semantic_errors(ranking(["South > Redwood and related", "Birch"]), query, tables)


def test_complete_list_is_binding_even_in_population_question(tmp_path):
    tables = table(tmp_path, ["Redwood and related", "Birch", "Elm", "Willow"])
    assert plan_review.semantic_errors(ranking(["Willow"]),
                                       "Rank by Score among these: Redwood, Birch, Elm.", tables)


@pytest.mark.parametrize("quote", ["", "'", "`", '"'])
def test_exact_compound_name_precedes_comma_separated_components(tmp_path, quote):
    compound = "Amber, Cobalt, and Jade"
    tables = table(tmp_path, [compound, "Amber", "Cobalt", "Jade", "Birch"])
    query = "Rank by Score: " + quote + compound + quote + ", Birch."
    assert not plan_review.semantic_errors(ranking([compound, "Birch"]), query, tables)
    assert plan_review.semantic_errors(ranking(["Amber", "Cobalt", "Jade", "Birch"]), query, tables)


@pytest.mark.parametrize("scope", ["", "within North "])
def test_repeated_labels_use_query_section_or_unique_overall_section(tmp_path, monkeypatch, scope):
    tables = table(tmp_path, ["Redwood and related", "Birch"] * 2)
    view = plan_review._view_for(ranking([]), tables)
    for i in view.df.index:
        section = "TOTAL" if i < 2 else "North"
        view.df.at[i, "_section"] = section
        view.df.at[i, "_row_path"] = section + " > " + view.df.at[i, "_label"]
    monkeypatch.setattr(plan_review, "_view_for", lambda *args: view)
    query = "Rank by Score " + scope + ": Redwood and Birch."
    good, bad = ("North", "TOTAL") if scope else ("TOTAL", "North")
    assert not plan_review.semantic_errors(ranking([good + " > Redwood and related", good + " > Birch"]), query, tables)
    assert plan_review.semantic_errors(ranking([bad + " > Redwood and related", bad + " > Birch"]), query, tables)


@pytest.mark.parametrize("name", ["Redwood", "Redwood and related"])
def test_unique_name_outside_requested_section_is_rejected(tmp_path, monkeypatch, name):
    tables = table(tmp_path, ["Redwood and related", "Birch"])
    view = plan_review._view_for(ranking([]), tables)
    for i, section in enumerate(["South", "North"]):
        view.df.at[i, "_section"] = section
        view.df.at[i, "_row_path"] = section + " > " + view.df.at[i, "_label"]
    monkeypatch.setattr(plan_review, "_view_for", lambda *args: view)
    query = "Rank by Score within North: " + name + " and Birch."
    bad = ranking(["South > Redwood and related", "North > Birch"])
    assert "explicit row name is outside the named section" in plan_review.semantic_errors(bad, query, tables)
