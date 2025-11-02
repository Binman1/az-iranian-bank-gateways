import logging
from json import dumps, loads
from time import gmtime, strftime

from zeep import Client, Transport

from azbankgateways.banks import BaseBank
from azbankgateways.exceptions import SettingDoesNotExist
from azbankgateways.exceptions.exceptions import BankGatewayRejectPayment
from azbankgateways.models import BankType, CurrencyEnum, PaymentStatus


class Parsian(BaseBank):
    _terminal_code = None
    _login_account = None

    def __init__(self, **kwargs):
        super(Parsian, self).__init__(**kwargs)
        self.set_gateway_currency(CurrencyEnum.IRR)
        self._payment_url = "https://pec.shaparak.ir/NewIPG/?token="

    def get_bank_type(self):
        return BankType.PARSIAN

    def set_default_settings(self):
        for item in ["TERMINAL_CODE", "LOGGIN_ACCOUNT"]:
            if item not in self.default_setting_kwargs:
                raise SettingDoesNotExist()
            setattr(self, f"_{item.lower()}", self.default_setting_kwargs[item])

    """
    gateway
    """

    @classmethod
    def get_minimum_amount(cls):
        return 1000

    def _get_gateway_payment_url_parameter(self):
        return self._payment_url + "".format(self.get_reference_number())

    def _get_gateway_payment_parameter(self):
        return {}

    def _get_gateway_payment_method_parameter(self):
        return "GET"

    """
    pay
    """

    def get_pay_data(self):
        data = {
            "requestData" : {
                "LoginAccount": int(self._login_account),
                "Originator": self.get_mobile_number(),
                "OrderId": int(self.get_tracking_code()),
                "Amount": int(self.get_gateway_amount()),
                "AdditionalData": dumps(self.get_custom_data()),
                "CallBackUrl": self._get_gateway_callback_url(),
            }
        }
        return data

    def prepare_pay(self):
        super(Parsian, self).prepare_pay()

    def pay(self):
        super(Parsian, self).pay()

        data = self.get_pay_data()
        client = self._get_pay_client()
        response = client.service.SalePayment(**data)
        try:
            status = response.Status
            token = response.Token
            if int(status) == 0 and int(token) > 0:
                self._set_reference_number(token)
                return True
            else:
                status_text = self._get_error_message(status)
                self._set_transaction_status_text(status_text)
                logging.critical(f"Parsian Error {status}: {status_text}")
                raise BankGatewayRejectPayment(self.get_transaction_status_text())
        except ValueError:
            error_msg = f"Connection error: {str(e)}"
        self._set_transaction_status_text(error_msg)
        logging.critical(error_msg)
        raise BankGatewayRejectPayment(error_msg)

    """
    verify from gateway
    """

    def prepare_verify_from_gateway(self):
        super(Parsian, self).prepare_verify_from_gateway()
        post = self.get_request().POST
        token = post.get("Token", None)
        order_id = post.get("OrderId", None)
        status = post.get("status", None)
        if not token:
            return
        self._set_reference_number(token)
        self._set_bank_record()
        self._bank.extra_information = dumps(dict(zip(post.keys(), post.values())))
        self._bank.save()

    def verify_from_gateway(self, request):
        super(Parsian, self).verify_from_gateway(request)

    """
    verify
    """

    def get_verify_data(self):
        super(Parsian, self).get_verify_data()
        data = {
            "requestData": {
                "LoginAccount": self._login_account,
                "Token": self.get_reference_number(),
            }
        }
        return data

    def prepare_verify(self, tracking_code):
        super(Parsian, self).prepare_verify(tracking_code)

    def verify(self, transaction_code):
        super(Parsian, self).verify(transaction_code)
        post = self.get_request().POST
        token = post.get("Token", None)
        order_id = post.get("OrderId", None)
        terminal_no = post.get("TerminalNo", None)
        rrn = post.get("RRN", None)
        status = post.get("status", None)
        amount_as_string = post.get("AmountAsString", None)
        discount_amount = post.get("DiscountAmount", None)

        if int(status) == -138:
            self._set_payment_status(PaymentStatus.CANCEL_BY_USER)
        elif int(status) == 0:
            if int(rrn) > 0:
                data = self.get_verify_data()
                client = self._get_verification_client()
                verify_result = client.service.ConfirmPayment(**data)
                if int(verify_result.Status) == 0 and int(verify_result.Token) > 0:
                    self._set_payment_status(PaymentStatus.COMPLETE)
                elif int(verify_result.Status) == -138:
                    self._set_payment_status(PaymentStatus.CANCEL_BY_USER)
                elif int(verify_result.Status) != 0:
                    self._set_payment_status(PaymentStatus.ERROR)
                    self._reversal_transaction()
        else:
            self._set_payment_status(PaymentStatus.ERROR)
            self._reversal_transaction()

    def _reversal_transaction(self):
        data = self.get_verify_data()
        client = self._get_reversal_client()
        client.service.ReversalRequest(**data)
        

    @staticmethod
    def _get_pay_client():
        transport = Transport(timeout=5, operation_timeout=5)
        client = Client("https://pec.shaparak.ir/NewIPGServices/Sale/SaleService.asmx?wsdl", transport=transport)
        return client

    @staticmethod
    def _get_verification_client():
        transport = Transport(timeout=5, operation_timeout=5)
        client = Client("https://pec.shaparak.ir/NewIPGServices/Confirm/ConfirmService.asmx?wsdl", transport=transport)
        return client
    
    def _get_reversal_client():
        transport = Transport(timeout=5, operation_timeout=5)
        client = Client("https://pec.shaparak.ir/NewIPGServices/Reversal/ReversalService.asmx?wsdl", transport=transport)
        return client

    @staticmethod
    def _get_current_time():
        return strftime("%H%M%S")

    @staticmethod
    def _get_current_date():
        return strftime("%Y%m%d", gmtime())

    def _get_sale_reference_id(self):
        extra_information = loads(getattr(self._bank, "extra_information", "{}"))
        return extra_information.get("SaleReferenceId", "1")

    def _get_error_message(self, error_code):
        """
        تبدیل کد خطا به پیام قابل فهم
        """
        error_messages = {
            '0': 'تراکنش با موفقیت انجام شد',
            '-1': 'اطلاعات ارسالی ناقص است',
            '-2': 'IP پذیرنده معتبر نیست',
            '-3': 'درگاه پرداخت فعال نیست',
            '-4': 'شناسه پذیرنده نامعتبر است',
            '-5': 'شناسه قبض نامعتبر است',
            '-6': 'شناسه پرداخت نامعتبر است',
            '-7': 'سیستم درگاه پرداخت قطع است',
            '-8': 'تراکنش قبلا برگشت خورده است',
            '-9': 'رسید دیجیتالی نامعتبر است',
            '-10': 'پذیرنده اجازه دسترسی به سرویس را ندارد',
            '-11': 'تراکنش یافت نشد',
            '-12': 'تراکنش قابل برگشت نیست',
            '-13': 'خطای داخلی سیستم',
            '-14': 'خطا در برقراری ارتباط با بانک',
            '-15': 'پاسخ بانک timeout شد',
            '-16': 'پارامترهای ارسالی نامعتبر است',
            '-17': 'سیستم موقتا قطع است',
            '-18': 'خطای سیستمی',
            '-19': 'آدرس بازگشت نامعتبر است',
            '-20': 'IP نامعتبر',
            '-21': 'مبلغ نامعتبر',
            '-22': 'بانک صادرکننده پاسخگو نیست',
            '-23': 'پذیرنده مسدود شده است',
            '-24': 'خطای امنیتی',
            '-25': 'اطلاعات کارت نادرست است',
            '-26': 'مبلغ تراکنش بیش از حد مجاز',
            '-112': 'شماره سفارش تکراری است',
            '-126': 'شناسه پذیرنده نامعتبر است',
            '-138': 'تراکنش توسط کاربر لغو شد'
        }
        
        return error_messages.get(str(error_code), f'خطای ناشناخته (کد: {error_code})')