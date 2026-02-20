#!/usr/bin/env python3

#   Copyright 2025 IQT Labs LLC
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

import argparse
import asyncio
import logging
import markdown2
import os
import time
import watchfiles

from ruamel.yaml import YAML
from nicegui import ui, app
from llm_snowglobe.core import Configuration, Database
from llm_snowglobe.scenario_loader import list_scenarios, load_scenario, merge_config
from llm_snowglobe.user_defined_game import UserDefinedGame

here = os.path.dirname(os.path.abspath(__file__))
datastep = 0

# Module-level game state shared across all browser clients
game_state = {}


def main(host="0.0.0.0", port=8000):
    parser = argparse.ArgumentParser()
    parser.add_argument(
      '-c','--config-file', help='location of a yaml config file', action='store',
      default='/config/game.yaml')
    parser.add_argument(
      '-l','--log-file', help='location of a file to log to', action='store',
      default='/home/snowglobe/logs/snowglobe-ui.log')
    args = parser.parse_args()

    cfg_file = args.config_file
    log_file = args.log_file
    logger = logging.getLogger(__name__)
    logging.basicConfig(filename=log_file, encoding='utf-8', level=logging.INFO, format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    logger.info('Logging started')

    # Load infra config
    yaml = YAML(typ="safe")
    with open(cfg_file, 'r') as f:
        infra_config = yaml.load(f)
    scenarios_dir = infra_config.get('scenarios_directory', '')

    async def detect_updates():
        while True:
            data_dir = game_state.get('data_dir')
            if not data_dir or not os.path.isdir(data_dir):
                await asyncio.sleep(2)
                continue
            globals()["datastep"] += 1
            async for changes in watchfiles.awatch(data_dir):
                break

    app.on_startup(detect_updates)

    app.add_static_file(url_path="/ai.png", local_file=os.path.join(here, "assets/ai.png"))
    app.add_static_file(
        url_path="/human.png", local_file=os.path.join(here, "assets/human.png")
    )

    def get_database():
        if 'db' in game_state:
            return game_state['db']
        # Fall back to file-based lookup (only works with full game configs)
        try:
            config = Configuration(cfg_file)
            with open(config.game_id_file,'r') as gif:
                ioid = gif.read()
            db = Database(ioid=ioid, path=config.data_dir)
            return db
        except (KeyError, FileNotFoundError):
            return None

    @ui.page("/")
    async def ui_page():

        # ── Setup Phase state ──
        scenario_data = {}      # loaded template dict
        role_toggles = {}       # {player_name: ui.toggle}
        advisor_checkboxes = {} # {advisor_name: ui.checkbox}
        moves_input = None
        scenario_preview = None

        async def on_scenario_selected(e):
            nonlocal scenario_data
            path = e.value
            if not path:
                return
            scenario_data = load_scenario(path)

            # Update preview
            scenario_preview.clear()
            with scenario_preview:
                # Scenario description card with scroll area
                with ui.card().classes('w-full'):
                    ui.label(scenario_data.get('title', '')).style('font-size: 20px; font-weight: bold')
                    desc = scenario_data.get('scenario', '')
                    with ui.scroll_area().style('max-height: 200px'):
                        ui.label(desc).style('white-space: pre-wrap; font-size: 14px; color: #555; line-height: 1.5')

                # Player roles card
                with ui.card().classes('w-full'):
                    ui.label('Player Roles').style('font-size: 18px; font-weight: bold')
                    role_toggles.clear()
                    for player_name in scenario_data.get('players', {}):
                        with ui.row().classes('items-center gap-4 py-1'):
                            ui.label(player_name).style('min-width: 220px; font-size: 15px')
                            toggle = ui.toggle(['AI', 'Human'], value='AI')
                            role_toggles[player_name] = toggle

                # Advisors card
                advisors = scenario_data.get('advisors', {})
                if advisors:
                    with ui.card().classes('w-full'):
                        ui.label('Advisors').style('font-size: 18px; font-weight: bold')
                        advisor_checkboxes.clear()
                        for advisor_name in advisors:
                            cb = ui.checkbox(advisor_name, value=True).style('font-size: 15px')
                            advisor_checkboxes[advisor_name] = cb

                # Moves card
                with ui.card().classes('w-full'):
                    ui.label('Number of Moves').style('font-size: 18px; font-weight: bold')
                    nonlocal moves_input
                    default_moves = scenario_data.get('moves', 6)
                    moves_input = ui.number(value=default_moves, min=1, max=50, step=1)

        async def start_game():
            if not scenario_data:
                ui.notify('Select a scenario first.', type='warning')
                return

            # Gather user choices
            roles = {}
            for player_name, toggle in role_toggles.items():
                roles[player_name] = 'human' if toggle.value == 'Human' else 'ai'

            active_advisors = [
                name for name, cb in advisor_checkboxes.items() if cb.value
            ]

            moves_val = int(moves_input.value) if moves_input else scenario_data.get('moves', 6)

            user_choices = {
                'roles': roles,
                'active_advisors': active_advisors,
                'moves': moves_val,
            }

            # Merge config layers
            merged = merge_config(scenario_data, infra_config, user_choices)
            config = Configuration.from_dict(merged)

            # Create the game
            sim = UserDefinedGame(config, logger)
            game_state['game'] = sim
            game_state['db'] = sim.db
            game_state['data_dir'] = config.data_dir

            # Notify human players of their ioids
            human_ioids = []
            for player_name, player_cfg in merged['players'].items():
                if player_cfg.get('kind') == 'human':
                    ioid = player_cfg.get('ioid', '')
                    human_ioids.append(f"{player_name}: {ioid}")
                    ui.notify(f'{player_name} — login with: {ioid}', type='positive', timeout=0, close_button='OK')

            logger.info(f"Game started: {merged.get('title', 'unknown')} | humans: {human_ioids}")

            # Launch the game coroutine and store the task reference
            task = asyncio.create_task(sim())
            game_state['task'] = task

            # Switch from setup to game UI
            setup_container.set_visibility(False)
            game_drawer.set_visibility(True)
            game_header.set_visibility(True)
            game_panels_container.set_visibility(True)

        async def stop_game(reset=False):
            """Cancel the running game, clean up, return to setup."""
            task = game_state.get('task')
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            # Deactivate advisors
            sim = game_state.get('game')
            if sim:
                for advisor in sim.advisors:
                    advisor.active = False
                try:
                    sim.db.commit()
                except Exception:
                    pass

            # Clear game state
            game_state.clear()

            # Hide game UI, show setup
            game_drawer.set_visibility(False)
            game_header.set_visibility(False)
            game_panels_container.set_visibility(False)
            setup_container.set_visibility(True)

            if reset:
                scenario_preview.clear()
                scenario_data.clear()
                role_toggles.clear()
                advisor_checkboxes.clear()

            ui.notify('Game stopped.', type='info')
            logger.info('Game stopped by user.')

        async def exit_server():
            """Shut down the NiceGUI server."""
            ui.notify('Server shutting down...', type='warning')
            logger.info('Server shutdown requested by user.')
            await asyncio.sleep(0.5)
            app.shutdown()

        # ── Login + display helpers (from original ui.py) ──

        db_ref = [None]  # mutable ref so inner functions see current db

        async def set_id(idval):
            db = get_database()
            db_ref[0] = db
            if len(idval) == 0:
                ui.notify("Enter your ID.")
            elif db.get_name(idval) is None:
                ui.notify("ID not found.")
            else:
                app.storage.tab["id"] = idval
                login_numb.text = app.storage.tab["id"]
                login_name.text = db.get_name(app.storage.tab["id"])
                preloginrow.set_visibility(False)
                postloginrow.set_visibility(True)
                new_resource_check()
                setup_tabs.refresh()
                setup_tab_panels.refresh()

        def _db():
            if db_ref[0] is not None:
                return db_ref[0]
            return get_database()

        def new_resource_check():
            db = _db()
            idval = app.storage.tab["id"]
            resource_string = ""
            for resource, resource_type in db.get_assignments(idval):
                resource_string += "|" + resource_type + ":" + resource
            key = "TABSTRING"
            new_resource = key not in tabvars or tabvars[key] != resource_string
            tabvars[key] = resource_string
            return new_resource

        @ui.refreshable
        def setup_tabs():
            db = _db()
            if db is None or "id" not in app.storage.tab:
                return
            idval = app.storage.tab["id"]
            icon = {
                "chatroom": "chat",
                "weblink": "language",
                "infodoc": "description",
                "notepad": "edit_note",
                "editdoc": "edit",
            }
            default_title = {
                "chatroom": "Chat",
                "weblink": "Data",
                "infodoc": "Info",
                "notepad": "Note",
                "editdoc": "Edit",
            }
            with ui.tabs().classes("w-full") as tabs:
                for resource, resource_type in db.get_assignments(idval):
                    properties = db.get_properties(resource)
                    if "title" in properties:
                        title = properties["title"]
                    else:
                        title = default_title[resource_type]
                    tabvars[resource] = {}
                    tabvars[resource]["tab"] = ui.tab(
                        resource, label=title, icon=icon[resource_type]
                    )
            tabvars["TABCONTAINER"] = tabs

        @ui.refreshable
        def setup_tab_panels():
            db = _db()
            if db is None or "id" not in app.storage.tab:
                return
            idval = app.storage.tab["id"]
            setup_func = {
                "chatroom": setup_chatroom,
                "weblink": setup_weblink,
                "infodoc": setup_infodoc,
                "notepad": setup_notepad,
                "editdoc": setup_editdoc,
            }
            with ui.tab_panels(tabvars["TABCONTAINER"]).classes("absolute-full") as panels:
                for resource, resource_type in db.get_assignments(idval):
                    setup_func[resource_type](resource)
            if len(tabvars) - 2 == 1:
                panels.set_value(resource)

        def setup_chatroom(resource):
            db = _db()
            with ui.tab_panel(tabvars[resource]["tab"]).classes("h-full"):
                with ui.column().classes("w-full items-center h-full"):
                    tabvars[resource]["message_count"] = 0
                    with ui.scroll_area().classes("w-full h-full border") as tabvars[
                        resource
                    ]["message_window"]:
                        tabvars[resource]["updater"] = ui.refreshable(display_messages)
                        tabvars[resource]["updater"](resource)
                    properties = db.get_properties(resource)
                    placeholder = (
                        properties["instruction"]
                        if "instruction" in properties
                        else "Ask the AI assistant."
                    )
                    tabvars[resource]["chattext"] = (
                        ui.textarea(placeholder=placeholder)
                        .classes("w-full border")
                        .style("height: auto; padding: 0px 5px")
                    )
                    button = ui.button(
                        "Send", on_click=lambda resource=resource: send_message(resource)
                    )
                    if (
                        "readonly" in properties
                        and "|%s|" % db.get_name(app.storage.tab["id"])
                        in properties["readonly"]
                    ):
                        button.enabled = False

        def setup_weblink(resource):
            with ui.tab_panel(tabvars[resource]["tab"]).classes("absolute-full"):
                tabvars[resource]["iframe"] = ui.element("iframe").classes(
                    "w-full h-full absolute-full"
                )
                display_weblink(resource)

        def setup_infodoc(resource):
            with ui.tab_panel(tabvars[resource]["tab"]).classes("absolute-full"):
                with ui.scroll_area().classes("w-full h-full absolute-full"):
                    tabvars[resource]["updater"] = ui.refreshable(display_infodoc)
                    tabvars[resource]["updater"](resource)

        def setup_notepad(resource):
            db = _db()
            with ui.tab_panel(tabvars[resource]["tab"]).classes("absolute-full"):
                tabvars[resource]["editor"] = (
                    ui.editor().classes("w-full h-full").props("height=100%")
                )
                tabvars[resource]["editor"]._props.update(
                    toolbar=[
                        [
                            {
                                "label": "Font",
                                "fixedLabel": True,
                                "fixedIcon": True,
                                "list": "no-icons",
                                "options": ["default_font", "arial", "times_new_roman"],
                            },
                            {
                                "label": "Size",
                                "fixedLabel": True,
                                "fixedIcon": True,
                                "list": "no-icons",
                                "options": [
                                    "size-1",
                                    "size-2",
                                    "size-3",
                                    "size-4",
                                    "size-5",
                                    "size-6",
                                    "size-7",
                                ],
                            },
                        ],
                        ["bold", "italic", "underline", "strike", "removeFormat"],
                        ["left", "center", "right", "justify"],
                        ["unordered", "ordered", "link", "hr"],
                        ["undo", "redo"],
                    ]
                )
                tabvars[resource]["editor"]._props.update(
                    fonts={
                        "arial": "Arial",
                        "times_new_roman": "Times New Roman",
                    }
                )
                display_notepad(resource)

        def setup_editdoc(resource):
            with ui.tab_panel(tabvars[resource]["tab"]).classes("absolute-full"):
                with ui.column().classes("w-full items-center h-full"):
                    tabvars[resource]["editobj"] = (
                        ui.textarea().classes("w-full").props("input-class=h-96")
                    )
                    display_editdoc(resource)
                    ui.button(
                        "Submit",
                        on_click=lambda resource=resource: submit_editdoc(resource),
                    )

        async def display_all():
            if "id" not in app.storage.tab:
                return
            db = _db()
            idval = app.storage.tab["id"]
            if new_resource_check():
                setup_tabs.refresh()
                setup_tab_panels.refresh()
                return
            display_func = {
                "chatroom": display_messages,
                "infodoc": display_infodoc,
            }
            for resource, resource_type in db.get_assignments(idval):
                if resource_type in display_func:
                    tabvars[resource]["updater"].refresh(resource)

        def display_messages(resource):
            db = _db()
            idval = app.storage.tab["id"]
            name = db.get_name(idval)
            chatlog = db.get_chatlog(resource)
            for message in chatlog:
                if "format" not in message or message["format"] == "plaintext":
                    text = message["content"]
                    text_html = False
                elif message["format"] == "markdown":
                    text = markdown2.markdown(message["content"])
                    text_html = True
                elif message["format"] == "html":
                    text = message["content"]
                    text_html = True
                sent = message["name"] == name
                ui.chat_message(
                    text=text,
                    name=message["name"],
                    stamp=message["stamp"],
                    avatar=message["avatar"],
                    sent=sent,
                    text_html=text_html,
                ).classes("w-full")
            if len(chatlog) > tabvars[resource]["message_count"]:
                tabvars[resource]["message_window"].scroll_to(percent=100)
                tabvars[resource]["message_count"] = len(chatlog)

        def display_weblink(resource):
            db = _db()
            properties = db.get_properties(resource)
            if "url" in properties:
                tabvars[resource]["iframe"].props("src=%s" % properties["url"])

        def display_infodoc(resource):
            db = _db()
            properties = db.get_properties(resource)
            if "content" not in properties:
                return
            if "format" not in properties or properties["format"] == "plaintext":
                ui.label(properties["content"]).style("white-space: pre-wrap").classes(
                    "w-full h-full"
                )
            elif properties["format"] == "markdown":
                ui.markdown(properties["content"]).classes("w-full h-full")
            elif properties["format"] == "html":
                ui.html(properties["content"]).classes("w-full h-full")

        def display_notepad(resource):
            db = _db()
            idval = app.storage.tab["id"]
            editor = tabvars[resource]["editor"]
            editor.bind_value(app.storage.general, resource)
            editor.on_value_change(lambda resource=resource: stamp_notepad(resource))
            properties = db.get_properties(resource)
            if (
                "readonly" in properties
                and "|%s|" % db.get_name(idval) in properties["readonly"]
            ):
                editor.enabled = False
            else:
                pass

        def display_editdoc(resource):
            db = _db()
            idval = app.storage.tab["id"]
            editobj = tabvars[resource]["editobj"]
            editobj.bind_value(app.storage.general, resource)
            properties = db.get_properties(resource)
            if (
                "readonly" in properties
                and "|%s|" % db.get_name(idval) in properties["readonly"]
            ):
                editobj.enabled = False

            handler_setup = """
            if (typeof window.editdoccursor == 'undefined') {
                window.editdoccursor = {};
            }
            if (typeof window.editdoccursor.%s == 'undefined') {
                window.editdoccursor.%s = {};
            }
            info = window.editdoccursor.%s;
            info.value = '';
            info.selectionStart = 0;
            info.selectionEnd = 0;
            info.selfChange = false;
            """ % tuple(
                [resource] * 3
            )
            text_change_handler = (
                """() => {
                window.editdoccursor.%s.selfChange = true;
            }"""
                % resource
            )
            cursor_move_handler = """() => {
                const element = getElement(%s).$refs.qRef.getNativeElement();
                const info = window.editdoccursor.%s;
                if (element.value !== info.value && !info.selfChange) {
                    var newStart = element.selectionStart;
                    var newEnd = element.selectionEnd;
                    if (element.value.substring(info.selectionStart + element.value.length - info.value.length) == info.value.substring(info.selectionStart)) {
                        newStart = info.selectionStart + element.value.length - info.value.length;
                    } else if (element.value.substring(0, info.selectionStart) == info.value.substring(0, info.selectionStart)) {
                        newStart = info.selectionStart;
                    }
                    if (element.value.substring(info.selectionEnd + element.value.length - info.value.length) == info.value.substring(info.selectionEnd)) {
                        newEnd = info.selectionEnd + element.value.length - info.value.length;
                    } else if (element.value.substring(0, info.selectionEnd) == info.value.substring(0, info.selectionEnd)) {
                        newEnd = info.selectionEnd;
                    }
                    if (newStart !== element.selectionStart || newEnd !== element.selectionEnd) {
                        element.setSelectionRange(newStart, newEnd);
                    }
                }
                info.value = element.value;
                info.selectionStart = element.selectionStart;
                info.selectionEnd = element.selectionEnd;
                info.selfChange = false;
            }""" % (
                editobj.id,
                resource,
            )
            ui.run_javascript(handler_setup)
            editobj.on("update:model-value", js_handler=text_change_handler)
            editobj.on("selectionchange", js_handler=cursor_move_handler)

        async def send_message(resource):
            db = _db()
            idval = app.storage.tab["id"]
            chattext = tabvars[resource]["chattext"]
            message = {
                "content": chattext.value.strip(),
                "format": "plaintext",
                "name": db.get_name(idval),
                "stamp": time.ctime(),
                "avatar": "human.png",
            }
            db.send_message(resource, **message)
            db.commit()
            tabvars[resource]["updater"].refresh(resource)
            chattext.set_value("")

        async def stamp_notepad(resource):
            tabvars[resource]["last_modified"] = time.time()

        async def save_notepad(resource):
            db = _db()
            now = time.time()
            if "last_modified" in tabvars[resource] and (
                "last_saved" not in tabvars[resource]
                or tabvars[resource]["last_saved"] < tabvars[resource]["last_modified"]
            ):
                content = tabvars[resource]["editor"].value
                stamp = time.ctime(tabvars[resource]["last_modified"])
                db.add_property(resource, "content", content)
                db.add_property(resource, "stamp", stamp)
                db.save_text(resource, content, None, stamp)
                db.commit()
                tabvars[resource]["last_saved"] = now

        async def submit_editdoc(resource):
            db = _db()
            idval = app.storage.tab["id"]
            content = app.storage.general[resource]
            name = db.get_name(idval)
            stamp = time.ctime()
            db.add_property(resource, "content", content)
            db.add_property(resource, "stamp", stamp)
            db.save_text(resource, content, name, stamp)
            db.commit()
            ui.notify("Document submitted")

        # ── Page Layout ──
        # NiceGUI requires drawer/header to be direct page children, not nested
        # in containers. We build both phases at page level and toggle visibility.

        tabvars = {}
        await ui.context.client.connected()

        # ── Phase 2 elements (game UI) — built first at page level, hidden ──

        # Left drawer (top-level layout element)
        game_drawer = ui.left_drawer(top_corner=True, bordered=True).classes("items-center")
        game_drawer.set_visibility(False)
        with game_drawer:
            with ui.column(align_items="center").classes("h-full"):
                ui.image(os.path.join(here, "assets/snowglobe.png")).props(
                    "width=150px"
                ).style("border-radius: 5%")
                ui.label("User Interface").style("font-size: 25px; font-weight: bold")
                ui.chip(
                    "Toggle Full Screen", color="#B4C7E7", on_click=ui.fullscreen().toggle
                )
                ui.chip("Toggle Dark Mode", color="#B4C7E7", on_click=ui.dark_mode().toggle)
                with ui.row() as preloginrow:
                    login_id = ui.input("ID", placeholder="User ID").props("size=5")
                    ui.chip(
                        "Log In", color="#B4C7E7", on_click=lambda: set_id(login_id.value)
                    )
                with ui.row() as postloginrow:
                    postloginrow.set_visibility(False)
                    login_numb = ui.label("ID")
                    login_name = ui.label("Name")

                # Game lifecycle controls
                ui.separator()
                with ui.column().classes('w-full items-center gap-2'):
                    ui.button('Stop Game', on_click=lambda: stop_game(reset=False), icon='stop').props('color=negative outline size=sm').classes('w-full')
                    ui.button('Reset to Setup', on_click=lambda: stop_game(reset=True), icon='restart_alt').props('color=warning outline size=sm').classes('w-full')

                ui.space().classes("h-100")
                ui.label("Do not input sensitive or personal information.").style(
                    "font-size: 12px; font-style: italic"
                )
                ui.input().bind_value(globals(), "datastep").on_value_change(
                    display_all
                ).set_visibility(
                    False
                )

        # Header (top-level layout element)
        game_header = ui.header().style("background-color: #B4C7E7")
        game_header.set_visibility(False)
        with game_header:
            setup_tabs()

        # Tab panels (top-level)
        game_panels_container = ui.column().classes('w-full h-full')
        game_panels_container.set_visibility(False)
        with game_panels_container:
            setup_tab_panels()

        # ── Phase 1: Setup Container (visible on load) ──
        with ui.column().classes('w-full items-center p-8 gap-6') as setup_container:
            ui.image(os.path.join(here, "assets/snowglobe.png")).props(
                "width=200px"
            ).style("border-radius: 5%")
            ui.label("Snow Globe").style("font-size: 32px; font-weight: bold")
            ui.label("Game Setup").style("font-size: 20px; color: #666")

            # Scenario selector card
            with ui.card().classes('w-full max-w-2xl'):
                ui.label('Select Scenario').style('font-size: 20px; font-weight: bold')
                scenarios = list_scenarios(scenarios_dir)
                if not scenarios:
                    ui.label('No scenarios found. Check scenarios_directory in config.').style('color: red')
                else:
                    options = {s[2]: s[1] for s in scenarios}  # {path: title}
                    ui.select(
                        options=options,
                        on_change=on_scenario_selected,
                    ).classes('w-full')

            # Dynamic preview area — populated when a scenario is selected
            scenario_preview = ui.column().classes('w-full max-w-2xl gap-4')

            # Action buttons
            with ui.row().classes('gap-4'):
                ui.button('Start Game', on_click=start_game, icon='play_arrow').props('color=primary size=lg')
                ui.button('Exit Server', on_click=exit_server, icon='power_settings_new').props('color=negative outline size=lg')


    def run(host="0.0.0.0", port=8000):
        ui.run(
            host=host,
            port=port,
            title="Snow Globe User Interface",
            favicon=os.path.join(here, "assets/favicon.ico"),
            reload=False,
        )

    run(host, port)

if __name__ in {"__main__", "__mp_main__"}:
    main()
