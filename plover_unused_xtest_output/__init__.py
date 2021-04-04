from plover.oslayer.xkeyboardcontrol import (
        KeyboardEmulation as DefaultKeyboardEmulation,
        KEY_TO_KEYSYM,
        with_display_lock,
        )
from time import sleep
from Xlib import X, XK, display
from Xlib.ext import xinput, xtest
from Xlib.ext.ge import GenericEventCode
from typing import Optional
from plover import log


class KeyboardEmulation(DefaultKeyboardEmulation):
    def _update_keymap(self)->None:
        try:
            super()._update_keymap()
        except AssertionError:
            pass
        self._backspace_mapping = None

        self._update_modifiers()  # TODO inefficient

        custom_keycodes = {mapping.keycode for mapping in self._custom_mappings_queue}
        modifier_keycodes = {keycode
                for keycodes in self.modifier_mapping
                for keycode in keycodes
                if keycode != 0}
        accepted_keycodes = custom_keycodes | modifier_keycodes

        self._keymap = {mapping.keysym: mapping
                for mapping in self._custom_mappings_queue + [*self._keymap.values()]
                if mapping.keycode in accepted_keycodes
                }
        self._custom_mappings_queue = [mapping
                for mapping in self._custom_mappings_queue
                if mapping.modifiers == 0
                ]
        # Some keys like arrow keys might not work properly if modifiers is not 0

    #@with_display_lock
    def send_backspaces(self, number_of_backspaces):
        for _ in range(number_of_backspaces):
            self.send_key_combination("backspace") # which locks the display lock

    def _get_mapping(self, keysym, automatically_map=True)->None:
        # NOTE copied from Plover source code.
        """Return a keycode and modifier mask pair that result in the keysym.

        There is a one-to-many mapping from keysyms to keycode and
        modifiers pairs; this function returns one of the possibly
        many valid mappings, or None if no mapping exists, and a
        new one cannot be added.

        Arguments:

        keysym -- A key symbol.

        """
        mapping = self._keymap.get(keysym)
        if mapping is None:
            # Automatically map?
            if not automatically_map:
                # No.
                print(f"no automatically map, {keysym}->None")
                return None
            # Can we map it?
            if 0 == len(self._custom_mappings_queue):
                # Nope...
                assert False
                return None
            mapping = self._custom_mappings_queue.pop(0)
            previous_keysym = mapping.keysym
            mapping.custom_mapping[0] = keysym
            mapping.custom_mapping[1] = keysym
            self._self_change.append(mapping.keycode)
            self._display.change_keyboard_mapping(mapping.keycode, [mapping.custom_mapping])
            print(f"new map {mapping.keycode} -> {keysym}")
            # Update our keymap.
            if previous_keysym in self._keymap:
                del self._keymap[previous_keysym]
            mapping.keysym = keysym
            self._keymap[keysym] = mapping
            log.debug('new mapping: %s', mapping)
            print('new mapping: %s'%mapping)
            # Move custom mapping back at the end of
            # the queue so we don't use it too soon.
            self._custom_mappings_queue.append(mapping)
        elif mapping.custom_mapping is not None:
            # Same as above; prevent mapping
            # from being reused to soon.
            self._custom_mappings_queue.remove(mapping)
            self._custom_mappings_queue.append(mapping)
        return mapping

    def _send_key_combo(self, key_events)->None:
        for keysym, pressed in key_events:
            # in this subclass (NOTE dirty hack), first item of key_events[i] is keysym
            assert isinstance(keysym, int)
            assert keysym is not None
            event_type = X.KeyPress if pressed else X.KeyRelease

            mapping_changed = False
            mapping = self._get_mapping(keysym, automatically_map=False)
            if mapping is None:
                mapping = self._get_mapping(keysym, automatically_map=True)
                assert mapping is not None
                mapping_changed = True
                if self._time_between_key_presses != 0:
                    self._display.sync()
                    sleep(self._time_between_key_presses / 2000)
                    print("!!sleep ", self._time_between_key_presses / 2000)

            keycode = mapping.keycode
            print('send: ', keycode, mapping)
            xtest.fake_input(self._display, event_type, keycode)

            if self._time_between_key_presses != 0:
                self._display.sync()
                sleep(self._time_between_key_presses / 2000 if mapping_changed
                        else self._time_between_key_presses / 1000)

    def _get_keycode_from_keystring(self, keystring)->Optional[int]:
        keysym = KEY_TO_KEYSYM.get(keystring)
        if keysym is None:
            return None
        return keysym # NOTE dirty hack

        # (about old method)
        # remap every time might not be safe
        # because it may be changed in subsequent map...?

        #mapping = self._get_mapping(keysym, automatically_map=True) # NOTE dirty hack
        #if mapping is None:
        #    return None
        #return mapping.keycode
