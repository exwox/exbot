"""
Base Exchange Client
Abstract class for all exchange integrations
"""
from abc import ABC, abstractmethod
from typing import Optional


class BaseExchangeClient(ABC):
    """Abstract base class for exchange clients"""

    def __init__(self, api_key: str = "", secret_key: str = ""):
        self.api_key = api_key
        self.secret_key = secret_key

    @abstractmethod
    def get_balance(self) -> dict:
        """Get account balance"""
        pass

    @abstractmethod
    def get_ticker(self, pair: str = 'btcidr') -> dict:
        """Get current ticker price"""
        pass

    @abstractmethod
    def buy(self, pair: str = 'btcidr', price: float = 0, amount: float = 0,
            client_order_id: str = '') -> dict:
        """Place buy limit order"""
        pass

    @abstractmethod
    def buy_market(self, pair: str = 'btcidr', amount_idr: float = 0,
                   client_order_id: str = '') -> dict:
        """Place buy market order"""
        pass

    @abstractmethod
    def sell(self, pair: str = 'btcidr', price: float = 0, amount: float = 0,
             client_order_id: str = '') -> dict:
        """Place sell limit order"""
        pass

    @abstractmethod
    def sell_market(self, pair: str = 'btcidr', crypto_amount: float = 0,
                    client_order_id: str = '') -> dict:
        """Place sell market order"""
        pass

    @abstractmethod
    def get_order_status(self, pair: str = 'btcidr', order_id: str = '') -> dict:
        """Get order status from exchange"""
        pass

    @abstractmethod
    def get_open_orders(self, pair: str = 'btcidr') -> list:
        """Get list of open orders"""
        pass

    @abstractmethod
    def cancel_order(self, pair: str = 'btcidr', order_id: str = '', order_type: str = '') -> dict:
        """Cancel an order"""
        pass

    @abstractmethod
    def get_trade_history(self, pair: str = 'btcidr', limit: int = 10) -> list:
        """Get trade history"""
        pass
