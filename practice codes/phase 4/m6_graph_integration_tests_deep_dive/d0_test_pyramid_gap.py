# Minimal demo of C1: two functions can each pass their OWN unit test, and still be
# WIRED together incorrectly. Only a test that checks the composed result -- an
# integration test -- catches that. No pytest needed: plain asserts, printed by hand.
def apply_discount(price, pct):
    # Subtracts a percentage off a price. Tested alone, in isolation, below.
    return price - price * pct  # e.g. 100 with 20% off -> 80.0


def add_shipping(price, flat_fee):
    # Adds a FLAT fee on top of a price -- not a percentage, so order will matter.
    return price + flat_fee  # e.g. 100 plus a $5 fee -> 105.0


def test_apply_discount_alone():
    # A UNIT test: one function, checked against its own, narrow contract.
    assert apply_discount(100, 0.20) == 80.0  # 20% off 100 is 80


def test_add_shipping_alone():
    # Another UNIT test: the other function, checked against ITS own contract.
    assert add_shipping(100, 5) == 105.0  # $5 shipping on top of 100 is 105


test_apply_discount_alone()  # run it -- passes
test_add_shipping_alone()  # run it -- passes
print("Both unit tests pass: each function does exactly what IT promises.")

# The business rule this checkout is supposed to follow: the discount applies to the
# PRODUCT price only -- shipping is added afterwards, at full price, never discounted.
correct_order = add_shipping(apply_discount(100, 0.20), 5)  # discount first, then shipping
wrong_order = apply_discount(add_shipping(100, 5), 0.20)  # shipping first, then discount

print(f"discount-then-shipping (the intended rule) : {correct_order}")  # (100*0.8)+5 = 85.0
print(f"shipping-then-discount (wired backwards)   : {wrong_order}")  # (100+5)*0.8 = 84.0


# THIS is the integration test: it checks the WIRING, not either function alone.
def test_checkout_never_discounts_the_shipping_fee():
    total = add_shipping(apply_discount(100, 0.20), 5)
    assert total == 85.0  # would FAIL if someone swapped the call order above


test_checkout_never_discounts_the_shipping_fee()  # passes only for the correct wiring
print("\nBoth functions were individually correct the whole time.")
print("Only a test of the COMPOSED result can tell the two wirings apart.")
