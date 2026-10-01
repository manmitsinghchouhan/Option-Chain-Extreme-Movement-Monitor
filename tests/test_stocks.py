from app.stocks import (
    FNO_STOCKS,
    INDEX_UNIVERSE,
    INDEX_SYMBOLS,
    get_symbols,
    get_stock_symbols,
    get_index_symbols,
    is_index_symbol,
    validate_stocks,
)


def test_stock_count():
    assert len(FNO_STOCKS) == 213
    assert len(INDEX_UNIVERSE) == 2
    assert len(get_symbols()) == 215


def test_index_classification():
    assert is_index_symbol("NIFTY") is True
    assert is_index_symbol("SENSEX") is True
    assert is_index_symbol("RELIANCE") is False
    assert get_index_symbols() == ["NIFTY", "SENSEX"]
    assert len(get_stock_symbols()) == 213


def test_stock_numbers_are_unique():
    symbols = get_symbols()
    assert len(symbols) == len(set(symbols))


def test_stock_symbols_are_not_empty():
    assert all(stock["symbol"].strip() for stock in FNO_STOCKS)


def test_stock_names_are_not_empty():
    assert all(stock["name"].strip() for stock in FNO_STOCKS)


def test_stock_configuration_is_valid():
    assert validate_stocks() == []
