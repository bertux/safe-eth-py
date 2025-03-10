import binascii
from typing import Optional, Union

from django.core import exceptions
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from eth_typing import ChecksumAddress
from eth_utils import to_normalized_address
from hexbytes import HexBytes

from ..utils import fast_bytes_to_checksum_address, fast_to_checksum_address
from .forms import EthereumAddressFieldForm, HexFieldForm, Keccak256FieldForm
from .validators import validate_checksumed_address

try:
    from django.db import DefaultConnectionProxy

    connection = DefaultConnectionProxy()
except ImportError:
    from django.db import connections

    connection = connections["default"]


class EthereumAddressField(models.CharField):
    default_validators = [validate_checksumed_address]
    description = "DEPRECATED. Use `EthereumAddressV2Field`. Ethereum address (EIP55)"
    default_error_messages = {
        "invalid": _('"%(value)s" value must be an EIP55 checksummed address.'),
    }

    def __init__(self, *args, **kwargs):
        kwargs["max_length"] = 42
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        del kwargs["max_length"]
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        value = super().to_python(value)
        if value:
            try:
                return fast_to_checksum_address(value)
            except ValueError:
                raise exceptions.ValidationError(
                    self.error_messages["invalid"],
                    code="invalid",
                    params={"value": value},
                )
        else:
            return value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return self.to_python(value)


class EthereumAddressV2Field(models.Field):
    default_validators = [validate_checksumed_address]
    description = "Ethereum address (EIP55)"
    default_error_messages = {
        "invalid": _('"%(value)s" value must be an EIP55 checksummed address.'),
    }

    def get_internal_type(self):
        return "BinaryField"

    def from_db_value(
        self, value: memoryview, expression, connection
    ) -> Optional[ChecksumAddress]:
        if value:
            return fast_bytes_to_checksum_address(value)

    def get_prep_value(self, value: ChecksumAddress) -> Optional[bytes]:
        if value:
            try:
                return HexBytes(to_normalized_address(value))
            except (TypeError, ValueError):
                raise exceptions.ValidationError(
                    self.error_messages["invalid"],
                    code="invalid",
                    params={"value": value},
                )

    def to_python(self, value) -> Optional[ChecksumAddress]:
        if value is not None:
            try:
                return fast_to_checksum_address(value)
            except ValueError:
                raise exceptions.ValidationError(
                    self.error_messages["invalid"],
                    code="invalid",
                    params={"value": value},
                )

    def formfield(self, **kwargs):
        defaults = {
            "form_class": EthereumAddressFieldForm,
            "max_length": 2 + 40,
        }
        defaults.update(kwargs)
        return super().formfield(**defaults)


class Uint256Field(models.DecimalField):
    """
    Field to store ethereum uint256 values. Uses Decimal db type without decimals to store
    in the database, but retrieve as `int` instead of `Decimal` (https://docs.python.org/3/library/decimal.html)
    """

    description = _("Ethereum uint256 number")

    def __init__(self, *args, **kwargs):
        kwargs["max_digits"] = 78  # 2 ** 256 is 78 digits
        kwargs["decimal_places"] = 0
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        del kwargs["max_digits"]
        del kwargs["decimal_places"]
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return int(value)

    def pre_save(self, model_instance, add):
        """
        Override pre_save to ensure that field is unsigned before save it
        :param model_instance:
        :param add:
        :return:
        """
        value = getattr(model_instance, self.attname)
        if value is not None and value < 0:
            raise ValidationError("Value must be an unsigned 256-bit integer")
        return super().pre_save(model_instance, add)


class Uint96Field(models.DecimalField):
    """
    Field to store ethereum uint96 values. Uses Decimal db type without decimals to store
    in the database, but retrieve as `int` instead of `Decimal` (https://docs.python.org/3/library/decimal.html)
    """

    description = _("Ethereum uint96 number")

    def __init__(self, *args, **kwargs):
        kwargs["max_digits"] = 29  # 2 ** 96 is 29 digits
        kwargs["decimal_places"] = 0
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        del kwargs["max_digits"]
        del kwargs["decimal_places"]
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return int(value)

    def pre_save(self, model_instance, add):
        """
        Override pre_save to ensure that field is unsigned before save it
        :param model_instance:
        :param add:
        :return:
        """
        value = getattr(model_instance, self.attname)
        if value is not None and value < 0:
            raise ValidationError("Value must be an unsigned 96-bit integer")
        return super().pre_save(model_instance, add)


class HexField(models.CharField):
    """
    Field to store hex values (without 0x). Returns hex with 0x prefix.

    On Database side a CharField is used.
    """

    description = "Stores a hex value into a CharField. DEPRECATED, use a BinaryField"

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        return value if value is None else HexBytes(value).hex()

    def get_prep_value(self, value):
        if value is None:
            return value
        elif isinstance(value, HexBytes):
            return value.hex()[
                2:
            ]  # HexBytes.hex() retrieves hexadecimal with '0x', remove it
        elif isinstance(value, bytes):
            return value.hex()  # bytes.hex() retrieves hexadecimal without '0x'
        else:  # str
            return HexBytes(value).hex()[2:]

    def formfield(self, **kwargs):
        # We need max_lenght + 2 on forms because of `0x`
        defaults = {"max_length": self.max_length + 2}
        # TODO: Handle multiple backends with different feature flags.
        if self.null and not connection.features.interprets_empty_strings_as_nulls:
            defaults["empty_value"] = None
        defaults.update(kwargs)
        return super().formfield(**defaults)

    def clean(self, value, model_instance):
        value = self.to_python(value)
        self.validate(value, model_instance)
        # Validation didn't work because of `0x`
        self.run_validators(value[2:])
        return value


class HexV2Field(models.BinaryField):
    def formfield(self, **kwargs):
        defaults = {
            "form_class": HexFieldForm,
        }
        defaults.update(kwargs)
        return super().formfield(**defaults)


class Sha3HashField(HexField):
    description = "DEPRECATED. Use `Keccak256Field`"

    def __init__(self, *args, **kwargs):
        kwargs["max_length"] = 64
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        del kwargs["max_length"]
        return name, path, args, kwargs


class Keccak256Field(models.BinaryField):
    description = "Keccak256 hash stored as binary"
    default_error_messages = {
        "invalid": _('"%(value)s" hash must be a 32 bytes hexadecimal.'),
        "length": _('"%(value)s" hash must have exactly 32 bytes.'),
    }

    def _to_bytes(self, value) -> Optional[bytes]:
        if value is None:
            return None
        else:
            try:
                result = HexBytes(value)
                if len(result) != 32:
                    raise exceptions.ValidationError(
                        self.error_messages["length"],
                        code="length",
                        params={"value": value},
                    )
                return result
            except (ValueError, binascii.Error):
                raise exceptions.ValidationError(
                    self.error_messages["invalid"],
                    code="invalid",
                    params={"value": value},
                )

    def from_db_value(self, value: memoryview, expression, connection) -> Optional[str]:
        if value:
            return HexBytes(value.tobytes()).hex()

    def get_prep_value(self, value: Union[bytes, str]) -> Optional[bytes]:
        if value:
            return self._to_bytes(value)

    def value_to_string(self, obj):
        return str(self.value_from_object(obj))

    def to_python(self, value) -> Optional[bytes]:
        if value is not None:
            try:
                return self._to_bytes(value)
            except (ValueError, binascii.Error):
                raise exceptions.ValidationError(
                    self.error_messages["invalid"],
                    code="invalid",
                    params={"value": value},
                )

    def formfield(self, **kwargs):
        defaults = {
            "form_class": Keccak256FieldForm,
            "max_length": 2 + 64,
        }
        defaults.update(kwargs)
        return super().formfield(**defaults)
