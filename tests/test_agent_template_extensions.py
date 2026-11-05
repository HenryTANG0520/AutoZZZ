from types import SimpleNamespace as NS
import unittest
from autozzz.agent_template_hotkeys import DEFAULTS,HotkeyPoller,parse_template_hotkey,validate_bindings
from autozzz.agent_template_registry import recognition,registrations,require_registered


class ExtendedTemplateHotkeyTests(unittest.TestCase):
    def test_fixed_source_key_names_have_actual_virtual_key_mapping(self):
        for text,key in [('Win+F13',124),('Ctrl+F24',135),('Alt+numpad_7',103),('Ctrl+Home',36),
                         ('Ctrl+page_down',34),('Alt+print_screen',44),('Win+media_play_pause',179),
                         ('Ctrl+vk_186',186),('Ctrl+caps_lock',20)]:
            with self.subTest(text=text):self.assertEqual(parse_template_hotkey(text)[-1],key)

    def test_reserved_keys_cannot_be_smuggled_as_virtual_codes(self):
        for key in ('Ctrl+F9','Win+F10','Ctrl+vk_120','Alt+vk_121','Win+vk_16'):
            with self.subTest(key=key),self.assertRaises(ValueError):parse_template_hotkey(key)

    def test_semantically_duplicate_aliases_are_rejected(self):
        with self.assertRaises(ValueError):validate_bindings(dict(DEFAULTS,battle_1='Ctrl+Home',battle_2='Ctrl+vk_36'))

    def test_right_windows_modifier_is_supported_and_release_is_edge_triggered(self):
        pressed=set();poller=HotkeyPoller(lambda key:0x8000 if key in pressed else 0)
        poller.configure(dict(capture='Win+F13'))
        pressed.update((92,124));self.assertEqual(poller.poll(True),['capture']);self.assertEqual(poller.poll(True),[])
        pressed.clear();poller.poll(True);pressed.update((91,124));self.assertEqual(poller.poll(True),['capture'])

    def test_extra_modifiers_do_not_trigger_and_inactive_edges_not_replayed(self):
        pressed=set();poller=HotkeyPoller(lambda key:0x8000 if key in pressed else 0)
        poller.configure(dict(capture='Ctrl+Home'))
        pressed.update((17,36,91));self.assertEqual(poller.poll(True),[])
        pressed.clear();poller.poll(True);pressed.update((17,36));self.assertEqual(poller.poll(False),[])
        self.assertEqual(poller.poll(True),[])

    def test_unsupported_or_invalid_names_rejected(self):
        for key in ('Home','Ctrl+mouse_left','Ctrl+numpad_10','Ctrl+vk_999','Ctrl+F25','Win+Win+1','Ctrl+vk_-1'):
            with self.subTest(key=key),self.assertRaises(ValueError):parse_template_hotkey(key)


class TemplateRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.agents=[NS(agent_id='known',agent_name='已有角色',template_id_list=['known','known_skin'],template_id='known_skin')]

    def test_registered_base_and_skin_map_to_same_existing_engine_agent(self):
        for name in ('known','known_skin'):
            row=require_registered(name,self.agents);self.assertEqual(row['agent_id'],'known');self.assertTrue(row['registered'])
        self.assertTrue(recognition('known_skin',self.agents)['selected'])
        self.assertFalse(recognition('known',self.agents)['selected'])

    def test_custom_id_cannot_claim_new_character_state_or_skills(self):
        row=recognition('known_new_skin_not_defined',self.agents)
        self.assertFalse(row['registered']);self.assertIsNone(row['agent_id'])
        with self.assertRaisesRegex(ValueError,'保存图片不能新增角色状态'):require_registered('new_character',self.agents)

    def test_ambiguous_engine_registration_rejected(self):
        self.agents.append(NS(agent_id='other',agent_name='另一个',template_id_list=['known_skin'],template_id='other'))
        with self.assertRaises(ValueError):registrations(self.agents)

    def test_unknown_placeholder_not_registered(self):
        agent=NS(agent_id='unknown',agent_name='未知',template_id_list=['unknown'],template_id='unknown')
        self.assertFalse(recognition('unknown',[agent])['registered'])
