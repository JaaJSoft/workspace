import json

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from workspace.passwords.services.vault import VaultService


@login_required
def index(request):
    vaults = list(VaultService.list_vaults(request.user))
    vaults_json = json.dumps([
        {
            'uuid': str(v.uuid),
            'name': v.name,
            'description': v.description,
            'icon': v.icon,
            'color': v.color,
            'is_setup': v.is_setup,
        }
        for v in vaults
    ])
    return render(request, 'passwords/ui/index.html', {'vaults_json': vaults_json})


@login_required
def vault_detail(request, uuid):
    vault = VaultService.get_vault(request.user, uuid)
    if vault is None:
        raise Http404
    vault_json = json.dumps({
        'uuid': str(vault.uuid),
        'name': vault.name,
        'description': vault.description,
        'icon': vault.icon,
        'color': vault.color,
        'is_setup': vault.is_setup,
        'kdf_algorithm': vault.kdf_algorithm,
        'kdf_iterations': vault.kdf_iterations,
        'kdf_salt': vault.kdf_salt,
        'protected_vault_key': vault.protected_vault_key,
    })
    return render(request, 'passwords/ui/vault.html', {'vault': vault, 'vault_json': vault_json})
