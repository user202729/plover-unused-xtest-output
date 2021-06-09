from time import sleep
from typing import Optional, Tuple, List, Union, Iterable, Dict, Deque
from collections import deque
import array

from Xlib import X, XK, display
from Xlib.ext import xinput, xtest
from Xlib.ext.ge import GenericEventCode

from plover.key_combo import add_modifiers_aliases, KeyCombo
from plover.oslayer.keyboardcontrol_base import KeyboardCaptureBase, KeyboardEmulationBase
from plover import log
from plover.oslayer.xkeyboardcontrol import (
        #KeyboardEmulation as DefaultKeyboardEmulation,
        KEY_TO_KEYSYM,
        with_display_lock,
        XEventLoop,
        keysym_to_string,
        uchr_to_keysym,
        )


def _trim_trailing_nosymbol(self, x: Iterable[int])->List[int]:
    y=list(x)
    while len(y)>8 and y[-1] in (X.NoSymbol):
        y.pop()
    return y


class KeyboardEmulation(XEventLoop, KeyboardEmulationBase):

    # Special keysym to mark custom keyboard mappings.
    PLOVER_MAPPING_KEYSYM = 0x01ffffff

    # Free unused keysym.
    UNUSED_KEYSYM = 0xffffff # XK_VoidSymbol

    def _get_keysym_from_keystring(self, keystring)->Optional[int]:
        '''Find the physical key <keystring> is mapped to.

        Return None of if keystring is not mapped.
        '''
        keysym = KEY_TO_KEYSYM.get(keystring)
        if keysym is None:
            return None
        return keysym

    def __init__(self, params=None)->None:
        """Prepare to emulate keyboard events."""
        assert not params
        XEventLoop.__init__(self, name='emulation')
        KeyboardEmulationBase.__init__(self, params)
        self._display = display.Display()
        self._key_combo = KeyCombo(self._get_keysym_from_keystring)
        self._custom_keycodes: Deque[int] = deque()
        self._time_between_key_presses = 0
        self._keymap: Dict[
            int, # keycode
            array.array

            # "I", for some reasons there are always many trailing 0 (X.NoSymbol)
            # they're insignificant
            # at most 8 can be set...?

            # [0] = Key
            # [1] = Shift+Key
            # [2] = Mode_switch+Key
            # [3] = Mode_switch+Shift+Key
            # [4] = ISO_Level3_Shift+Key
            # [5] = ISO_Level3_Shift+Shift+Key

            # ISO_Level3_Shift = AltGr, Mode_switch = "deprecated AltGr"
            # usually they're not used...?
            # https://unix.stackexchange.com/questions/55076/what-is-the-mode-switch-modifier-for

            ] = {}

    def start(self)->None:
        self._update_keymap()
        self._update_modifiers()
        KeyboardEmulationBase.start(self)
        XEventLoop.start(self)

    def cancel(self)->None:
        KeyboardEmulationBase.cancel(self)
        XEventLoop.cancel(self)

    def set_time_between_key_presses(self, ms)->None:
        self._time_between_key_presses = ms

    def _on_event(self, event)->None:
        if event.type == X.MappingNotify:
            if event.request == X.MappingKeyboard:
                self._update_keymap()

            elif event.request == X.MappingModifier:
                self._update_modifiers()

    @with_display_lock
    def _update_keymap(self)->None:
        '''Analyse keymap, build a mapping of keysym to (keycode + modifiers),
        and find unused keycodes that can be used for unmapped keysyms.
        '''
        keycode = self._display.display.info.min_keycode
        keycode_count = self._display.display.info.max_keycode - keycode + 1
        self._keymap = dict(zip(
            range(keycode, keycode+keycode_count),
            self._display.get_keyboard_mapping(keycode, keycode_count)
            ))

        self._custom_keycodes.clear()
        for keycode, keysyms in self._keymap.items():
            if self.PLOVER_MAPPING_KEYSYM in keysyms or all(x==X.NoSymbol for x in keysyms):
                if keycode!=8:
                    # TODO temporary workaround
                    # this is usually free and safe to use, but mapping this will make
                    # xdotool and similar programs send it instead of the "correct" keycode,
                    # which will make capturing harder.
                    self._custom_keycodes.append(keycode)
        print(f"custom =  {self._custom_keycodes}")

    @with_display_lock
    def _update_modifiers(self)->None:
        # Get modifier mapping.
        self.modifier_mapping: List[array.array  # "B"
                ] = self._display.get_modifier_mapping()

    def _send_keycode(self, keycode: int, modifiers: int=0)->None:
        """Emulate a key press and release.

        Arguments:

        keycode -- An integer in the inclusive range [8-255].

        modifiers -- An 8-bit bit mask indicating if the key
        pressed is modified by other keys, such as Shift, Capslock,
        Control, and Alt.

        """
        modifiers_list = [
            self.modifier_mapping[n][0]
            for n in range(8)
            if (modifiers & (1 << n))
        ]
        print(f"== send  {keycode}")
        # Press modifiers.
        for mod_keycode in modifiers_list:
            xtest.fake_input(self._display, X.KeyPress, mod_keycode)
        # Press and release the base key.
        xtest.fake_input(self._display, X.KeyPress, keycode)
        xtest.fake_input(self._display, X.KeyRelease, keycode)
        # Release modifiers.
        for mod_keycode in reversed(modifiers_list):
            xtest.fake_input(self._display, X.KeyRelease, mod_keycode)

    def _try_send_char_without_change_map(self, keysym: int)->bool:
        """Try to send a key code that corresponds to keysym without changing the keyboard map.
        DO NOT SUPPORT MODIFIERS.
        Returns True on success.
        """
        for keycode in self._custom_keycodes:
            keysyms = self._keymap[keycode]
            for index, modifiers in (
                    (0, 0),
                    (1, X.ShiftMask),
                    (4, X.Mod5Mask),
                    (5, X.Mod5Mask | X.ShiftMask),
                    ):
                # NOTE uppercase/lowercase will be broken if existing keymap is malformed
                if keysyms[index] == keysym:
                    self._send_keycode(keycode, modifiers)
                    return True
        return False

    def _get_custom_keycode(self)->int:
        """
        Cyclically return free keycodes from a list.
        """
        keycode: int = self._custom_keycodes.popleft()
        self._custom_keycodes.append(keycode)
        return keycode

    def _custom_map(self, keysyms: Iterable[int])->int:
        """Create a custom mapping with keysyms.
        Return the mapped keycode.
        """
        keysyms_ = array.array('I', keysyms)
        assert len(keysyms_)==8
        keycode: int = self._get_custom_keycode()
        assert self.PLOVER_MAPPING_KEYSYM in keysyms_
        self._keymap[keycode] = keysyms_
        self._display.change_keyboard_mapping(keycode, [keysyms_])
        return keycode

    @with_display_lock
    def send_string(self, s)->None:
        """Emulate the given string.

        The emulated string is not detected by KeyboardCapture.

        Argument:

        s -- The string to emulate.

        """
        for char in s:
            keysym = uchr_to_keysym(char)
            if self._try_send_char_without_change_map(keysym):
                self._display.sync()
                sleep(self._time_between_key_presses / 1000)
            else:
                print(f"new map for char =  {char}")
                keycode = self._custom_map([keysym] + [self.PLOVER_MAPPING_KEYSYM]*7)
                self._display.sync()
                sleep(self._time_between_key_presses / 2000)
                self._send_keycode(keycode, 0)
                self._display.sync()
                sleep(self._time_between_key_presses / 2000)

        self._display.sync()

    def _clone_mapping(self, keycode: int)->int:
        # truncate 8 or longer, leave place for PLOVER_MAPPING_KEYSYM
        return self._custom_map([*self._keymap[keycode][:7], self.PLOVER_MAPPING_KEYSYM])

    def _find_matching_keycode(self, keysym: int, is_custom: Optional[bool])->Optional[int]:
        # is_custom: filter on whether it's in one of Plover's custom mappings.
        # None: no filter, True/False: must be equal to that value.

        modifier_keycodes: array.array  # "B"
        for modifier_keycodes in self.modifier_mapping:
            for keycode in modifier_keycodes:
                if keycode in self._keymap and self._keymap[keycode][0] == keysym:
                    print(f"_find_matching_keycode:  {keysym}  -> mod  {keycode}")
                    return keycode  # select, send actual modifier key

        # clone the key map to a custom mapping, then send
        for index in range(8):
            # prefer better match at lower modifier index
            # then prefer Plover custom ones
            for keycode, keysyms in self._keymap.items():
                if keysyms[index]==keysym:
                    is_custom_actual = self.PLOVER_MAPPING_KEYSYM in keysyms
                    assert is_custom_actual == (keycode in self._custom_keycodes)
                    if is_custom is None or is_custom_actual == is_custom:
                        print(f"_find_matching_keycode:  {keysym}  ->  {keycode} | custom =  {is_custom}")
                        return keycode

            # note: {#braceleft} will send bracketleft, not shift+bracketleft
            # this is compatible with current Plover behavior

        print(f"_find_matching_keycode: no match ==  {keysym}")
        return None

    # NOT lock display, DO sync display
    def _send_key_combo(self, key_events: List[Tuple[int, int]])->None:
        for keysym, pressed in key_events:
            event_type = X.KeyPress if pressed else X.KeyRelease

            keycode: Optional[int]
            automatically_mapped: bool

            keycode_actual = self._find_matching_keycode(keysym, is_custom=False)
            if keycode_actual is None:
                print(f"not a key on the keyboard, custom map it")
                keycode = self._custom_map([keysym, keysym] + [self.PLOVER_MAPPING_KEYSYM]*6)
                automatically_mapped = True
            else:
                print(f"is a key on the keyboard, check if it's already custom-mapped")
                keycode_custom = self._find_matching_keycode(keysym, is_custom=True)
                if keycode_custom is not None and self._keymap[keycode_actual][:7] == self._keymap[keycode_custom][:7]:
                    print(f"reuse, good enough")
                    keycode = keycode_custom
                    automatically_mapped = False
                else:
                    print(f"not good enough {keycode_custom} " + str(
                        (self._keymap[keycode_actual][:7], self._keymap[keycode_custom][:7])
                        if keycode_custom
                        else None
                        ))

                    keycode = self._clone_mapping(keycode_actual)
                    automatically_mapped = True

            assert keycode is not None
            if automatically_mapped:
                self._display.sync()
                sleep(self._time_between_key_presses / 2000)

            print(f"== #send {'down' if pressed else 'up'} {keycode}")
            xtest.fake_input(self._display, event_type, keycode)

            self._display.sync()
            if automatically_mapped:
                sleep(self._time_between_key_presses / 2000)
            else:
                sleep(self._time_between_key_presses / 1000)

        if key_events:
            self._display.sync()

    @with_display_lock
    def send_key_combination(self, combo_string)->None:
        """Emulate a sequence of key combinations.

        KeyboardCapture instance would normally detect the emulated
        key events. In order to prevent this, all KeyboardCapture
        instances are told to ignore the emulated key events.

        Argument:

        combo_string -- A string representing a sequence of key
        combinations. Keys are represented by their names in the
        Xlib.XK module, without the 'XK_' prefix. For example, the
        left Alt key is represented by 'Alt_L'. Keys are either
        separated by a space or a left or right parenthesis.
        Parentheses must be properly formed in pairs and may be
        nested. A key immediately followed by a parenthetical
        indicates that the key is pressed down while all keys enclosed
        in the parenthetical are pressed and released in turn. For
        example, Alt_L(Tab) means to hold the left Alt key down, press
        and release the Tab key, and then release the left Alt key.

        """
        self._send_key_combo(self._key_combo.parse(combo_string))

    #@with_display_lock
    def send_backspaces(self, number_of_backspaces)->None:
        # TODO
        for x in range(number_of_backspaces):
            self.send_key_combination("backspace")

