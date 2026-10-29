"""Gestão de usuários disponível apenas para administradores."""
from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from .config import OPERATIONAL_DB_FILE
from .user_store import (
    PERMISSOES_DISPONIVEIS, cadastrar_usuario, pode_acessar_aba,
    listar_usuarios, atualizar_usuario, excluir_usuario,
)


class UserManagementTab:
    def __init__(self, parent, identity):
        if not pode_acessar_aba(identity, 'usuarios'):
            raise PermissionError('Somente administradores podem acessar usuários')
        self.parent = parent
        self.identity = identity
        self.selected_id = None
        self.records = {}
        self.usuario = tk.StringVar(master=parent)
        self.senha = tk.StringVar(master=parent)
        self.confirmacao = tk.StringVar(master=parent)
        self.status = tk.StringVar(master=parent)
        ttk.Label(parent, text='Gerenciar usuários', style='Title.TLabel').pack(anchor='w')
        columns = ('usuario', 'permissoes')
        listing = ttk.Frame(parent)
        listing.pack(fill='x', pady=10)
        self.table = ttk.Treeview(listing, columns=columns, show='headings', height=5, selectmode='browse')
        self.table.heading('usuario', text='Usuário')
        self.table.heading('permissoes', text='Permissões')
        self.table.column('usuario', width=150, stretch=False)
        self.table.column('permissoes', width=450)
        scrollbar = ttk.Scrollbar(listing, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side='left', fill='x', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.table.bind('<<TreeviewSelect>>', self.select_user)
        actions = ttk.Frame(parent)
        actions.pack(fill='x')
        ttk.Button(actions, text='Novo usuário', command=self.new_user).pack(side='left')
        ttk.Button(actions, text='Atualizar lista', command=self.refresh).pack(side='left', padx=8)
        self.delete_button = ttk.Button(actions, text='Excluir selecionado', command=self.delete, state='disabled')
        self.delete_button.pack(side='left')
        form = ttk.Frame(parent)
        form.pack(anchor='w', pady=10)
        for row, (label, variable, masked) in enumerate((
            ('Usuário', self.usuario, False),
            ('Senha (mínimo de 8 caracteres)', self.senha, True),
            ('Confirmar senha', self.confirmacao, True),
        )):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=4)
            ttk.Entry(form, textvariable=variable, show='•' if masked else '', width=32).grid(
                row=row, column=1, sticky='w', pady=4)
        ttk.Label(parent, text='Ao editar, deixe a senha vazia para manter a atual.').pack(anchor='w')
        ttk.Label(parent, text='Permissões').pack(anchor='w', pady=(10, 4))
        self.permissoes = {}
        for key, label in PERMISSOES_DISPONIVEIS:
            variable = tk.BooleanVar(master=parent, value=False)
            self.permissoes[key] = variable
            ttk.Checkbutton(parent, text=label, variable=variable).pack(anchor='w', pady=2)
        self.button = ttk.Button(parent, text='Criar usuário', command=self.create)
        self.button.pack(anchor='w', pady=(10, 0))
        ttk.Label(parent, textvariable=self.status, wraplength=600).pack(anchor='w', pady=10)
        self.refresh()

    def refresh(self):
        try:
            records = listar_usuarios(OPERATIONAL_DB_FILE, self.identity.get('id'))
        except (PermissionError, ValueError, sqlite3.Error, OSError):
            self.table.delete(*self.table.get_children())
            self.records = {}
            self.new_user()
            self.status.set('Não foi possível listar usuários. Verifique o banco e sua permissão.')
            return
        self.table.delete(*self.table.get_children())
        self.records = {row['id']: row for row in records}
        labels = dict(PERMISSOES_DISPONIVEIS)
        for row in records:
            self.table.insert('', 'end', iid=str(row['id']), values=(
                row['usuario'], ', '.join(labels.get(p, p) for p in row['permissoes'])))
        self.new_user()

    def new_user(self):
        self.selected_id = None
        selection = self.table.selection()
        if selection:
            self.table.selection_remove(*selection)
        self.usuario.set('')
        self.senha.set('')
        self.confirmacao.set('')
        self.status.set('')
        for variable in self.permissoes.values():
            variable.set(False)
        self.button.configure(text='Criar usuário')
        self.delete_button.configure(state='disabled')

    def select_user(self, event=None):
        selection = self.table.selection()
        if not selection:
            return
        row = self.records.get(int(selection[0]))
        if row is None:
            return
        self.selected_id = row['id']
        self.usuario.set(row['usuario'])
        self.senha.set('')
        self.confirmacao.set('')
        self.status.set('')
        for key, variable in self.permissoes.items():
            variable.set(key in row['permissoes'])
        self.button.configure(text='Salvar alterações')
        self.delete_button.configure(state='disabled' if row['id'] == self.identity.get('id') else 'normal')

    def create(self):
        if self.senha.get() != self.confirmacao.get():
            self.status.set('As senhas não coincidem.')
            return
        permissions = [key for key, variable in self.permissoes.items() if variable.get()]
        self.button.configure(state='disabled')
        try:
            if self.selected_id is None:
                cadastrar_usuario(OPERATIONAL_DB_FILE, self.identity.get('id'),
                                  self.usuario.get(), self.senha.get(), permissions)
                message = 'Usuário criado com sucesso.'
            else:
                atualizar_usuario(OPERATIONAL_DB_FILE, self.identity.get('id'), self.selected_id,
                                  self.usuario.get(), permissions, self.senha.get() or None)
                if self.selected_id == self.identity.get('id'):
                    self.identity.update(usuario=self.usuario.get().strip(), permissoes=permissions)
                message = 'Usuário atualizado com sucesso. O acesso será aplicado no próximo login.'
            self.refresh()
            self.status.set(message)
        except sqlite3.IntegrityError:
            self.status.set('Este usuário já existe ou os dados são inválidos.')
        except (ValueError, PermissionError) as exc:
            self.status.set(str(exc))
        except (sqlite3.Error, OSError):
            self.status.set('Não foi possível salvar o usuário no banco de dados.')
        finally:
            self.senha.set('')
            self.confirmacao.set('')
            self.button.configure(state='normal')

    def delete(self):
        if self.selected_id is None:
            return
        row = self.records.get(self.selected_id)
        if row is None or not messagebox.askyesno(
            'Excluir usuário', f'Excluir o usuário {row["usuario"]}?', parent=self.parent,
        ):
            return
        try:
            excluir_usuario(OPERATIONAL_DB_FILE, self.identity.get('id'), self.selected_id)
        except (ValueError, PermissionError) as exc:
            self.status.set(str(exc))
        except (sqlite3.Error, OSError):
            self.status.set('Não foi possível excluir o usuário.')
        else:
            self.refresh()
            self.status.set('Usuário excluído com sucesso.')
