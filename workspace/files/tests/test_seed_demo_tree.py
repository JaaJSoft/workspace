"""``scripts/seed_demo.py`` must be able to fill a file tree at any seed.

The seeder draws folder names from a 20-entry pool tens of times per user, so
two draws landing on the same (parent, name) pair is ordinary rather than
rare - and the service rightly refuses the duplicate, which used to abort the
whole run with a half-populated database behind it.

The script is loaded from its path: it lives outside the ``workspace``
package, has no ``__init__.py`` beside it, and bootstraps Django at import.
"""

import importlib.util
import random
import sys
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.files.models import File

REPO_ROOT = Path(__file__).resolve().parents[3]
SEEDER_SOURCE = REPO_ROOT / "scripts" / "seed_demo.py"

User = get_user_model()


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_demo", SEEDER_SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SeedDemoFileTreeTests(TestCase):
    """Regression coverage for the sibling-name collision in build_file_tree."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.seeder = _load_seeder()

    def test_build_file_tree_completes_for_every_seed(self):
        # Seeding the global RNG is what makes the collision reproducible;
        # hand the state back so no later test inherits it.
        self.addCleanup(random.setstate, random.getstate())

        # A fresh user per seed: the generator only guarantees a tree it built
        # itself is coherent, and the seeder never calls it twice for one user.
        for seed in range(8):
            with self.subTest(seed=seed):
                user = User.objects.create_user(
                    username=f"seeded{seed}", email=f"seeded{seed}@demo.local"
                )
                random.seed(seed)
                self.seeder.build_file_tree(user, 5, 50, max_depth=4, history_days=30)

                names = list(
                    File.objects.filter(owner=user).values_list("parent_id", "name")
                )
                folded = [(parent, name.lower()) for parent, name in names]
                self.assertEqual(
                    len(folded),
                    len(set(folded)),
                    "two siblings share a name - the service would have refused one",
                )
