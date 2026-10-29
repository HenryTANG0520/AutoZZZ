from types import SimpleNamespace
import unittest

from autozzz.onedragon_library_rules import coordinate, data_tree, library_name, route_selection


class LibraryRulesTests(unittest.TestCase):
    def test_white_order_and_black_exclusion_use_same_route_identity(self):
        routes = [SimpleNamespace(full_id=key) for key in ('a_1', 'b_1', 'c_2')]
        white = SimpleNamespace(list_type='whitelist', route_items=['c_2', 'a_1'])
        black = SimpleNamespace(list_type='blacklist', route_items=['b_1'])
        self.assertEqual([r.full_id for r in route_selection(routes, white)], ['c_2', 'a_1'])
        self.assertEqual([r.full_id for r in route_selection(routes, black)], ['a_1', 'c_2'])
        self.assertIs(route_selection(routes, white)[0], routes[-1])

    def test_missing_duplicate_and_invalid_route_groups_are_not_silently_filtered(self):
        routes = [SimpleNamespace(full_id='a_1')]
        for kind, rows in (('whitelist', ['missing']), ('blacklist', ['missing']), ('whitelist', ['a_1', 'a_1']), ('unknown', []), ('whitelist', 'a_1')):
            with self.subTest(kind=kind, rows=rows), self.assertRaises(ValueError):
                route_selection(routes, SimpleNamespace(list_type=kind, route_items=rows))

    def test_coordinates_are_original_integer_pixels(self):
        for value in (0, '0', 99, '99'): self.assertEqual(coordinate(value, 100), int(value))
        for value in (-1, 100, '1.5', 1.5, True, 'NaN', '９'):
            with self.subTest(value=value), self.assertRaises(ValueError): coordinate(value, 100)

    def test_paths_and_windows_reserved_names_are_rejected(self):
        for name in ('我的方案', 'name_2-copy', '[区域] 路线'): self.assertEqual(library_name(name), name)
        for name in ('../outside', 'CON.txt', 'LPT1', 'x\ny', 'x\0y', '.', ' name', 'name.', 'example.sample', 'a'*101):
            with self.subTest(name=name), self.assertRaises(ValueError): library_name(name)

    def test_import_tree_rejects_cycles_implicit_objects_nonfinite_and_large_inputs(self):
        data_tree(dict(author=['作者'], settings=[True, None, 1.5]))
        cycle=[];cycle.append(cycle)
        for value in (cycle, dict(x=float('inf')), dict(x=object()), {1:'invalid field'}):
            with self.assertRaises(ValueError): data_tree(value)
        with self.assertRaises(ValueError): data_tree([1, 2, 3], limit=3)
