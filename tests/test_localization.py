"""Language selection/presentation separation; no game or UI windows."""
import importlib
import tkinter as tk
import unittest
from unittest.mock import patch
from autozzz import localization as lang
from autozzz.localization_tk import PresentationVar,display_status,install_tk_localization


class LocalizationTests(unittest.TestCase):
    def test_explicit_language_does_not_change_default(self):
        with patch.object(lang,'_language','zh'):
            self.assertEqual(lang.ui_language('en'),'en')
            self.assertEqual(lang.ui_language(),'zh')
            with self.assertRaises(ValueError):lang.ui_language('fr')

    def test_read_preference_once(self):
        with patch.object(lang,'_language',None),patch('autozzz.appearance_preferences.effective_preferences',return_value={'language':'en'})as read:
            self.assertEqual(lang.ui_language(),'en')
            for _ in range(100):self.assertEqual(lang.tr('设置'),'Settings')
            read.assert_called_once_with()

    def test_auto_and_scope(self):
        with patch.object(lang,'system_ui_language',return_value='en'),patch.object(lang,'_language','zh'):
            self.assertEqual(lang.ui_language('auto'),'en')
            with lang.language_scope('auto'):
                self.assertEqual(lang.tr('设置'),'Settings')
                with lang.language_scope('zh'):self.assertEqual(lang.tr('设置'),'设置')
                self.assertEqual(lang.ui_language(),'en')
            self.assertEqual(lang.ui_language(),'zh')

    def test_format_values_are_never_translated(self):
        with lang.language_scope('en'):
            self.assertEqual(lang.tr('正在执行：{name}',name='设置 坏女人'),'Running: 设置 坏女人')
            self.assertEqual(lang.tr('用户文件 /设置/坏女人.json'),'用户文件 /设置/坏女人.json')
            self.assertEqual(lang.tr('应用配置…'),'App settings…')
            self.assertEqual(lang.application_name('custom','设置',True),'设置')
            self.assertEqual(lang.application_name('email','邮件奖励'),'Mail rewards')

    def test_only_explicit_templates_in_statuses(self):
        with lang.language_scope('en'):
            self.assertEqual(display_status('正在执行：设置 坏女人'),'Running: 设置 坏女人')
            self.assertEqual(display_status('国服 · 自动进入游戏'),'CN · Enter game automatically')
            self.assertEqual(display_status('未知日志：设置'),'未知日志：设置')

    def test_presentation_var_preserves_python_value_without_traces(self):
        interp=tk.Tcl()
        with lang.language_scope('en'):
            value=PresentationVar(interp,'准备就绪')
            self.assertEqual(value.get(),'准备就绪');self.assertEqual(value.display,'Ready')
            self.assertEqual(value.trace_info(),[])
            value.set('正在执行：坏女人');self.assertEqual(value.get(),'正在执行：坏女人')
            self.assertEqual(value.display,'Running: 坏女人')
            value.set_status('已暂停','设置')
            self.assertEqual(value.get(),'已暂停 · 设置');self.assertEqual(value.display,'Paused · 设置')
            raw=tk.StringVar(interp,'设置');self.assertEqual(raw.get(),'设置')

    def test_catalog_templates_have_matching_placeholders(self):
        import string
        from autozzz.localization_catalog import EN_UI
        from autozzz.localization_native import EN_NATIVE
        def names(text):return [key for _,key,_,_ in string.Formatter().parse(text)if key is not None]
        for source,target in {**EN_UI,**EN_NATIVE}.items():self.assertCountEqual(names(source),names(target),source)
        self.assertGreater(len(EN_UI),700)

    def test_install_is_idempotent_and_does_not_patch_inputs(self):
        from tkinter import ttk
        insert,item,setvalue=tk.Text.insert,ttk.Treeview.item,tk.StringVar.set
        install_tk_localization();label=ttk.Label.__init__;install_tk_localization()
        self.assertIs(label,ttk.Label.__init__)
        self.assertIs(insert,tk.Text.insert);self.assertIs(item,ttk.Treeview.item);self.assertIs(setvalue,tk.StringVar.set)


if __name__=='__main__':unittest.main()
