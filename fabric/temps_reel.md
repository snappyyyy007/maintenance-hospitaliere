# Temps réel : capteurs de fuite d'eau

Des capteurs simulés envoient leurs lectures dans Fabric. Une règle KQL confirme les vraies fuites et Activator envoie une alerte.

```
simuler_capteurs.py ──► Eventstream ──► Eventhouse (KQL) ──► Activator ──► courriel
```

## Le scénario

40 capteurs fictifs envoient une lecture toutes les 30 secondes. Le scénario est fixe et contient de quoi piéger une règle trop simple :

| Situation | Dans le fichier de démo | Ce que la règle doit faire |
|---|---|---|
| Fuite au pavillon Héritage | `CAP-021`, local `B-607` | Confirmer |
| Propagation à l'étage du dessous | `CAP-019`, local `B-505`, 6 minutes plus tard | Confirmer |
| Fuite au bloc opératoire | `CAP-024`, local `C-527` | Confirmer, urgence critique |
| Fuite dans un bureau | `CAP-036`, local `F-228` | Confirmer |
| Fausses alarmes de 1 ou 2 lectures | 4 capteurs | Ignorer |
| Lecture unique reçue 3 fois | Une de ces 4 fausses alarmes | Ignorer, grâce au dédoublonnage |
| Capteur qui se tait, batterie à 3 % | `CAP-016` | Signaler comme silencieux |

Les messages peuvent aussi se perdre, arriver en double ou arriver dans le désordre.

**Règle :** une fuite est confirmée à la 3e détection en 2 minutes ou moins sur un même capteur. Les doublons sont retirés avant le calcul.

## État des tests

`scripts/tester_capteurs.py` vérifie le simulateur et une traduction Python de la règle. Il ne couvre pas :

- l'exécution des requêtes KQL, dont c'est ici le premier vrai test ;
- l'envoi réel, testé seulement avec un faux Eventstream ;
- les écrans de ce guide, qui n'ont pas été suivis dans Fabric : les libellés peuvent différer.

## 1. Créer la base

1. Dans ton espace de travail, crée un **Eventhouse** nommé `eh_capteurs`. Il crée une base KQL du même nom.
2. Ouvre un ensemble de requêtes sur cette base et colle le contenu de `requetes.kql`.
3. Exécute le bloc 1, qui crée la table `capteurs_fuite`.

## 2. Valider les requêtes avec le fichier de démo

Cette étape teste le KQL sans dépendre du flux en direct.

1. Sur la base, choisis **Get data**, puis **Local file**.
2. Sélectionne la table `capteurs_fuite`, téléverse `data/capteurs/evenements_demo.jsonl` et choisis le format JSON.
3. Vérifie que la table contient 4 725 lignes.
4. Exécute les blocs 2, 3 et 4, et compare-les à `data/capteurs/attendu_kql.csv` : 4 fuites, 4 fausses alarmes, 1 capteur silencieux.
5. Vide ensuite la table. Sinon, les événements de démo fausseraient les blocs qui partent du dernier événement.

```kql
.clear table capteurs_fuite data
```

## 3. Créer le flux

1. Crée un **Eventstream** nommé `es_capteurs`.
2. Ajoute une source **Custom endpoint**.
3. Ajoute une destination **Eventhouse**, vers la table `capteurs_fuite`, au format JSON.
4. Publie l'Eventstream.
5. Sélectionne la source, choisis **SAS Key Authentication** et copie **Connection string-primary key**.

## 4. Lancer le simulateur

Cette chaîne de connexion est un secret. Ne la colle jamais dans un fichier du dépôt, car ce dépôt est public. Si elle fuit, régénère-la.

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install azure-eventhub
```

Cette commande attend que tu colles la chaîne, sans l'afficher ni la garder dans l'historique :

```bash
read -rs EVENTSTREAM_CONNEXION && export EVENTSTREAM_CONNEXION
```

```bash
.venv/bin/python scripts/simuler_capteurs.py --minutes 15
```

Si `pip` refuse d'installer le paquet, crée le `.venv` avec une version plus récente de Python.

Le script affiche les fuites prévues et leur heure. Les événements doivent apparaître dans la table après quelques secondes. Les blocs 2 à 6 fonctionnent alors sur les données en direct : ce sont les mêmes capteurs que dans le fichier de démo, seules les heures changent.

## 5. Créer l'alerte

1. Exécute le bloc 7 dans l'ensemble de requêtes.
2. Choisis **Set alert** dans le ruban.
3. Règle la fréquence d'exécution à 5 minutes.
4. Choisis une notification par courriel, avec `local_id`, `type_local` et `urgence` dans le message.
5. Relance le simulateur. Chaque fuite doit produire exactement un courriel, 6 minutes au plus après sa confirmation.

Le bloc 7 est écrit pour une exécution toutes les 5 minutes. Si tu changes la fréquence, change aussi `5m` dans la requête, sinon des fuites seront manquées ou répétées.

### Pourquoi la requête est écrite ainsi

- **Une alerte par fuite :** Activator envoie une alerte pour chaque ligne retournée. Sans `est_premiere`, une fuite de 20 minutes enverrait plusieurs courriels.
- **Marge de 1 minute :** la fenêtre s'arrête avant maintenant, pour qu'un message en retard ne fasse pas manquer une fuite.
- **Recul de 15 minutes :** il permet de reconnaître une fuite déjà en cours, pour ne pas la signaler de nouveau.

Une exécution aux 5 minutes veut dire jusqu'à 6 minutes d'attente, ce qui est long pour un bloc opératoire. En production, une alerte branchée directement sur l'Eventstream réagirait plus vite, mais elle devrait compter elle-même les détections. C'est un bon compromis à expliquer en entrevue.

## 6. Tout arrêter après la démo

Une requête exécutée toutes les 1 ou 5 minutes garde l'Eventhouse actif en permanence, ce qui consomme de la capacité. Après ta démo, désactive l'alerte et arrête l'Eventstream.

## En cas d'erreur

| Symptôme | Cause probable |
|---|---|
| Le script demande `EVENTSTREAM_CONNEXION` | La variable n'est pas définie dans ce terminal |
| Aucune ligne dans la table | Eventstream non publié, ou destination en erreur |
| `horodatage` vide | Le format de date n'a pas été lu : vérifie le mappage JSON de la table |
| Le bloc 2 renvoie plus de 4 fuites sur la démo | Le fichier a été chargé deux fois, ou des événements en direct sont mêlés |
| Le bloc 7 reste vide sur la démo | C'est normal : ces événements sont dans le passé |
