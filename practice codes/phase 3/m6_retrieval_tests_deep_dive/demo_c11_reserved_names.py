# Run with: pytest demo_c11_reserved_names.py -q -p no:cacheprovider
# pytest calls module-level functions with these names as setup/teardown hooks:
#   setup_module / teardown_module / setup_function / teardown_function
# Any OBJECT bound to such a name is treated as a hook -- including an imported module.
import json as setup_module          # the P3-M6 slip, in miniature (was: import src.vectorstore.setup as setup_module)


def test_anything():
    assert setup_module.dumps({}) == "{}"
