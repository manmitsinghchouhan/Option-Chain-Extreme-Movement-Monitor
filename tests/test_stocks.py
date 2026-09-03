from app.stocks import FNO_STOCKS, get_symbols, validate_stocks


def test_stock_count():
    assert len(FNO_STOCKS) == 210


def test_stock_numbers_are_unique():
    symbols = get_symbols()
    assert len(symbols) == len(set(symbols))


def test_stock_symbols_are_not_empty():
    assert all(stock["symbol"].strip() for stock in FNO_STOCKS)


def test_stock_names_are_not_empty():
    assert all(stock["name"].strip() for stock in FNO_STOCKS)


def test_stock_configuration_is_valid():
    assert validate_stocks() == []
