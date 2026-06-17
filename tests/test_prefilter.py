"""Юнит-тесты пред-фильтра (логика, на которой держится юнит-экономика).

Запуск:  python tests/test_prefilter.py   (или  python -m pytest tests/)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.prefilter import PreFilter

KW = ["нужен бот", "ищу разработчика", "настроить crm", "n8n"]
STOP = ["вакансия", "резюме", "бесплатно", "курс"]
pf = PreFilter(KW, STOP)


def test_matches_keyword():
    assert pf.match("Срочно нужен бот для заявок!") == "нужен бот"

def test_case_and_punctuation_insensitive():
    assert pf.match("ИЩУ  Разработчика,  опыт нужен") == "ищу разработчика"

def test_yo_normalization():
    pf2 = PreFilter(["ещё бот"], [])
    assert pf2.match("нужен еще бот") == "еще бот"

def test_no_keyword_returns_none():
    assert pf.match("Просто обсуждаем погоду в чате") is None

def test_stop_word_blocks_even_with_keyword():
    assert pf.match("Вакансия: ищу разработчика в штат") is None

def test_stop_substring_blocks():
    pf2 = PreFilter(["нужен бот"], ["обучение"])
    assert pf2.match("нужен бот, обучение в подарок") is None

def test_empty_and_none():
    assert pf.match("") is None
    assert pf.match(None) is None

def test_latin_keyword():
    assert pf.match("кто настраивал n8n?") == "n8n"

def test_keyword_without_stop_passes():
    assert pf.match("нужен бот для приёма заявок") == "нужен бот"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"  ok  {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}  — {e}")
    print(f"\n{passed}/{len(fns)} прошло")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
