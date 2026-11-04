#!/usr/bin/env python3
"""Génère un jeu de données FICTIF de bons de travail pour un hôpital imaginaire.

Aucune donnée réelle : établissement, pavillons, locaux et techniciens sont inventés.
Le résultat est reproductible (graine fixe) et volontairement imparfait : des défauts
de qualité sont injectés pour donner du travail à la couche silver.

Usage : python3 scripts/generer_donnees.py [--graine 42] [--sortie data/brut]
"""
import argparse
import csv
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

DEBUT = date(2023, 10, 1)
FIN = date(2026, 9, 30)
DATE_EXTRACTION = datetime(2026, 9, 30, 23, 59)

# id, nom, vocation, année, superficie m², étages, facteur de vétusté
PAVILLONS = [
    ("PAV-A", "Pavillon des Érables", "Soins aigus", 2014, 62000, 9, 0.70),
    ("PAV-B", "Pavillon Héritage", "Soins de longue durée", 1958, 31000, 7, 1.90),
    ("PAV-C", "Pavillon du Fleuve", "Chirurgie et bloc opératoire", 1987, 44000, 8, 1.20),
    ("PAV-D", "Pavillon Boréal", "Mère-enfant", 2014, 38000, 6, 0.65),
    ("PAV-E", "Pavillon de la Recherche", "Laboratoires", 1996, 27000, 6, 0.95),
    ("PAV-F", "Pavillon Laurentien", "Ambulatoire et imagerie", 1972, 23000, 5, 1.50),
    ("PAV-G", "Centrale énergétique", "Services techniques", 1972, 9000, 3, 1.30),
]

# type de local, criticité (1 à 3), poids dans le parc
TYPES_LOCAUX = [
    ("Unité de soins", 2, 34), ("Bureau", 1, 20), ("Corridor", 1, 10),
    ("Salle mécanique", 2, 8), ("Laboratoire", 2, 7), ("Imagerie", 2, 5),
    ("Cuisine", 2, 4), ("Urgence", 3, 4), ("Bloc opératoire", 3, 3),
    ("Soins intensifs", 3, 3), ("Stérilisation", 3, 2),
]

# catégorie -> taux quotidien de base, saisonnalité par mois (janv. à déc.)
CATEGORIES = {
    "Plomberie": (2.5, [1.2, 1.2, 1.1, 1.0, 0.9, 0.9, 0.9, 0.9, 0.9, 1.0, 1.0, 1.1]),
    "Électricité": (2.0, [1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.2, 1.2, 1.0, 1.0, 1.0, 1.0]),
    "CVC": (2.6, [1.5, 1.4, 1.0, 0.8, 0.9, 1.4, 1.8, 1.7, 1.0, 0.8, 1.0, 1.3]),
    "Dégât d'eau": (0.32, [1.7, 1.5, 2.2, 2.4, 1.1, 0.8, 0.8, 0.8, 0.7, 0.7, 0.8, 1.2]),
}

# sous-catégorie, poids, gabarits de description
PROBLEMES = {
    "Plomberie": [
        ("Fuite de tuyau", 26, ["Fuite sous le lavabo", "Tuyau qui goutte au plafond", "Fuite à un raccord en cuivre"]),
        ("Drain bouché", 30, ["Drain de douche bouché", "Évier qui se vide mal", "Refoulement au drain de plancher"]),
        ("Toilette défectueuse", 24, ["Toilette qui coule sans arrêt", "Chasse d'eau brisée", "Toilette bouchée"]),
        ("Eau chaude insuffisante", 12, ["Pas d'eau chaude à l'unité", "Eau tiède aux robinets"]),
        ("Robinet défectueux", 8, ["Robinet à capteur inactif", "Robinet qui ne ferme plus"]),
    ],
    "Électricité": [
        ("Éclairage défectueux", 42, ["Néons qui clignotent", "Luminaire éteint", "Ballast à remplacer"]),
        ("Prise défectueuse", 24, ["Prise murale sans courant", "Prise d'urgence rouge inactive"]),
        ("Disjoncteur déclenché", 18, ["Disjoncteur qui saute à répétition", "Perte de courant dans une section"]),
        ("Alimentation d'urgence", 6, ["Essai de génératrice en échec", "Alarme au panneau d'urgence"]),
        ("Appel infirmier", 10, ["Cloche d'appel inactive", "Voyant d'appel en panne"]),
    ],
    "CVC": [
        ("Température inadéquate", 44, ["Local trop chaud", "Local trop froid", "Plaintes sur la température"]),
        ("Ventilation faible", 20, ["Peu d'air aux diffuseurs", "Odeur persistante, ventilation faible"]),
        ("Bruit anormal", 12, ["Bruit fort dans le conduit", "Vibration à l'unité de toit"]),
        ("Panne de refroidisseur", 10, ["Alarme sur le refroidisseur", "Climatisation arrêtée"]),
        ("Pression différentielle", 14, ["Alarme de pression de salle", "Pression négative hors plage"]),
    ],
    "Dégât d'eau": [
        ("Infiltration par le toit", 26, ["Eau au plafond après la pluie", "Tuiles de plafond tachées"]),
        ("Rupture de conduite", 24, ["Conduite rompue, eau au sol", "Eau qui coule dans le mur"]),
        ("Infiltration par la fondation", 18, ["Eau au sous-sol après le dégel", "Plancher humide au niveau bas"]),
        ("Débordement", 20, ["Débordement d'évier, étage inondé", "Refoulement d'égout"]),
        ("Gicleur déclenché", 12, ["Tête de gicleur brisée", "Écoulement au réseau de gicleurs"]),
    ],
}

# Échelle de temps de la prise en charge (assignation, début des travaux)
ECHELLE_DELAI_HEURES = {"P1": 4, "P2": 24, "P3": 120, "P4": 720}
# priorité, libellé, cible de fermeture en heures
PRIORITES = [("P1", "Urgence", 12), ("P2", "Élevée", 48), ("P3", "Normale", 120), ("P4", "Planifiée", 720)]
SOURCES = [("Portail web", 52), ("Téléphone", 34), ("Système GTB", 14)]
METIERS = {"Plomberie": "Plombier", "Électricité": "Électricien", "CVC": "Frigoriste",
           "Dégât d'eau": "Plombier"}

# Variantes d'écriture rencontrées dans un vieux système de saisie
VARIANTES_CATEGORIE = {
    "Plomberie": ["PLOMBERIE", "plomberie ", "Plomb."],
    "Électricité": ["Electricite", "ELEC", "électricité"],
    "CVC": ["cvc", "HVAC", "C.V.C."],
    "Dégât d'eau": ["Degat d'eau", "Dégâts d'eau", "DEGAT EAU"],
}

COLONNES_BONS = [
    "bon_id", "bon_parent_id", "date_creation", "date_assignation", "date_debut_travaux",
    "date_fermeture", "statut", "type_bon", "priorite", "categorie", "sous_categorie",
    "pavillon_id", "etage", "local_id", "source", "description", "technicien_id",
    "heures_main_oeuvre", "cout_main_oeuvre", "cout_pieces", "cout_sous_traitance",
]


def choix_pondere(rng, items, poids):
    return rng.choices(items, weights=poids, k=1)[0]


def poisson(rng, lam):
    seuil, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= seuil:
            return k
        k += 1


def creer_locaux(rng):
    locaux = []
    for pav_id, _, _, _, superficie, etages, _ in PAVILLONS:
        for _ in range(superficie // 300):
            etage = rng.randint(0, etages - 1)
            type_local, criticite, _ = choix_pondere(rng, TYPES_LOCAUX, [t[2] for t in TYPES_LOCAUX])
            numero = f"{etage}{rng.randint(1, 60):02d}"
            locaux.append({
                "local_id": f"{pav_id[-1]}-{numero}", "pavillon_id": pav_id, "etage": etage,
                "type_local": type_local, "criticite": criticite,
                "poids": 1 / rng.uniform(0.15, 1.0),
            })
    # Le numéro est tiré au hasard : on garde une seule fiche par local_id
    return list({l["local_id"]: l for l in locaux}.values())


def creer_techniciens(rng):
    techniciens, numero = [], 1
    for metier, nombre, taux in [("Plombier", 9, 46.5), ("Électricien", 8, 47.8), ("Frigoriste", 9, 49.2)]:
        for i in range(nombre):
            externe = i >= nombre - 2
            techniciens.append({
                "technicien_id": f"TECH-{numero:03d}", "metier": metier,
                "quart": choix_pondere(rng, ["Jour", "Soir", "Nuit"], [65, 25, 10]),
                "statut_emploi": "Sous-traitant" if externe else "Interne",
                "taux_horaire": round(taux * (1.75 if externe else 1) + rng.uniform(-2, 2), 2),
            })
            numero += 1
    return techniciens


def heure_creation(rng, jour, priorite):
    if priorite == "P1":
        heure = rng.randint(0, 23)
    else:
        heure = min(23, max(0, int(rng.gauss(11, 3))))
    return datetime(jour.year, jour.month, jour.day, heure, rng.randint(0, 59))


def priorite_de(rng, categorie, criticite, type_bon):
    if type_bon == "Préventif":
        return "P4"
    poids = {"Dégât d'eau": [35, 45, 18, 2]}.get(categorie, [4, 18, 58, 20])
    if criticite == 3:
        poids = [poids[0] * 4, poids[1] * 2, poids[2], poids[3] * 0.3]
    return choix_pondere(rng, ["P1", "P2", "P3", "P4"], poids)


def couts(rng, categorie, sous_categorie, priorite):
    heures = rng.lognormvariate(1.0 if categorie != "Dégât d'eau" else 2.2, 0.7)
    pieces = rng.lognormvariate(4.3, 1.1) if rng.random() < 0.65 else 0.0
    sous_traitance = 0.0
    if categorie == "Dégât d'eau":
        sous_traitance = rng.lognormvariate(8.3, 0.8)  # assèchement, décontamination
    elif sous_categorie in ("Panne de refroidisseur", "Alimentation d'urgence"):
        pieces *= 4
        if rng.random() < 0.5:
            sous_traitance = rng.lognormvariate(7.4, 0.6)
    if priorite == "P1":
        heures *= 1.3
    return round(max(heures, 0.25), 2), round(pieces, 2), round(sous_traitance, 2)


def creer_bon(rng, creation, categorie, sous_categorie, description, local, type_bon,
              techniciens_par_metier, parent=None):
    priorite = priorite_de(rng, categorie, local["criticite"], type_bon)
    creation = heure_creation(rng, creation, priorite) if isinstance(creation, date) \
        and not isinstance(creation, datetime) else creation
    echelle = ECHELLE_DELAI_HEURES[priorite]
    # Les vieux pavillons ferment plus lentement (accès, pièces introuvables)
    vetuste = next(p[6] for p in PAVILLONS if p[0] == local["pavillon_id"])
    assignation = creation + timedelta(hours=rng.lognormvariate(math.log(echelle * 0.12), 0.8))
    debut = assignation + timedelta(hours=rng.lognormvariate(math.log(echelle * 0.25), 0.9))
    attente_pieces = rng.uniform(72, 500) if rng.random() < 0.08 and priorite != "P1" else 0
    heures, pieces, sous_traitance = couts(rng, categorie, sous_categorie, priorite)
    fermeture = debut + timedelta(hours=heures * 1.4 * (0.6 + 0.4 * vetuste) + attente_pieces)
    technicien = rng.choice(techniciens_par_metier[METIERS[categorie]])

    bon = {
        "_creation": creation, "_parent": parent, "bon_parent_id": "",
        "date_assignation": assignation, "date_debut_travaux": debut, "date_fermeture": fermeture,
        "statut": "Fermé", "type_bon": type_bon, "priorite": priorite, "categorie": categorie,
        "sous_categorie": sous_categorie, "pavillon_id": local["pavillon_id"], "etage": local["etage"],
        "local_id": local["local_id"],
        "source": choix_pondere(rng, [s[0] for s in SOURCES], [s[1] for s in SOURCES]),
        "description": description, "technicien_id": technicien["technicien_id"],
        "heures_main_oeuvre": heures,
        "cout_main_oeuvre": round(heures * technicien["taux_horaire"], 2),
        "cout_pieces": pieces, "cout_sous_traitance": sous_traitance,
    }
    if type_bon == "Préventif":
        bon["source"], bon["description"] = "Planifié", f"Entretien préventif : {sous_categorie.lower()}"
        bon["cout_sous_traitance"] = 0.0
    if rng.random() < 0.02:
        annuler(bon)
    else:
        appliquer_coupure(bon)
    return bon


def annuler(bon):
    bon["statut"] = "Annulé"
    for champ in ("date_debut_travaux", "date_fermeture", "technicien_id", "heures_main_oeuvre",
                  "cout_main_oeuvre", "cout_pieces", "cout_sous_traitance"):
        bon[champ] = ""


def appliquer_coupure(bon):
    """Ce qui n'a pas encore eu lieu à la date d'extraction reste vide."""
    if bon["date_fermeture"] <= DATE_EXTRACTION:
        return
    bon["date_fermeture"] = ""
    for champ in ("heures_main_oeuvre", "cout_main_oeuvre", "cout_pieces", "cout_sous_traitance"):
        bon[champ] = ""
    if bon["date_assignation"] > DATE_EXTRACTION:
        bon["statut"], bon["date_assignation"] = "Ouvert", ""
        bon["date_debut_travaux"], bon["technicien_id"] = "", ""
    elif bon["date_debut_travaux"] > DATE_EXTRACTION:
        bon["statut"], bon["date_debut_travaux"] = "Assigné", ""
    else:
        bon["statut"] = "En cours"


def tirer_probleme(rng, categorie):
    options = PROBLEMES[categorie]
    nom, _, gabarits = choix_pondere(rng, options, [o[1] for o in options])
    return nom, rng.choice(gabarits)


def jours():
    jour = DEBUT
    while jour <= FIN:
        yield jour
        jour += timedelta(days=1)


def generer_courants(rng, locaux, tech):
    """Flux quotidien : saison, jour de semaine et vétusté du pavillon."""
    vetuste = {p[0]: p[6] for p in PAVILLONS}
    bons = []
    poids_locaux = {}
    for categorie in CATEGORIES:
        # Les dégâts d'eau et la plomberie touchent davantage les vieux bâtiments
        exposant = 1.6 if categorie in ("Plomberie", "Dégât d'eau") else 0.8
        poids_locaux[categorie] = [l["poids"] * vetuste[l["pavillon_id"]] ** exposant for l in locaux]

    for jour in jours():
        facteur_jour = 0.45 if jour.weekday() >= 5 else 1.1
        for categorie, (taux, saison) in CATEGORIES.items():
            lam = taux * saison[jour.month - 1] * facteur_jour
            if categorie == "Dégât d'eau":
                lam /= facteur_jour  # l'eau ne prend pas congé la fin de semaine
            for _ in range(poisson(rng, lam)):
                local = rng.choices(locaux, weights=poids_locaux[categorie], k=1)[0]
                preventif = categorie in ("CVC", "Électricité") and rng.random() < 0.18
                sous_categorie, description = tirer_probleme(rng, categorie)
                bon = creer_bon(rng, jour, categorie, sous_categorie, description, local,
                                "Préventif" if preventif else "Correctif", tech)
                bons.append(bon)
    return bons


def generer_points_chauds(rng, locaux, tech):
    """Problèmes chroniques : même local, même panne, à intervalles rapprochés."""
    candidats = [l for l in locaux if l["pavillon_id"] in ("PAV-B", "PAV-F", "PAV-C")]
    bons, verite = [], []
    for local in rng.sample(candidats, 14):
        categorie = choix_pondere(rng, ["Plomberie", "CVC", "Électricité"], [55, 30, 15])
        sous_categorie, description = tirer_probleme(rng, categorie)
        jour = DEBUT + timedelta(days=rng.randint(0, 700))
        occurrences = 0
        for _ in range(rng.randint(6, 14)):
            if jour > FIN:
                break
            bons.append(creer_bon(rng, jour, categorie, sous_categorie, description, local, "Correctif", tech))
            occurrences += 1
            jour += timedelta(days=rng.randint(8, 28))
        verite.append({"local_id": local["local_id"], "categorie": categorie,
                       "sous_categorie": sous_categorie, "occurrences": occurrences})
    return bons, verite


def generer_suites_degats(rng, bons, locaux, tech):
    """Un dégât d'eau entraîne souvent des bons liés : cause à l'étage du dessus, électricité."""
    par_pavillon = {}
    for local in locaux:
        par_pavillon.setdefault(local["pavillon_id"], []).append(local)
    suites = []
    for parent in [b for b in bons if b["categorie"] == "Dégât d'eau" and b["statut"] != "Annulé"]:
        if rng.random() > 0.55:
            continue
        voisins = par_pavillon[parent["pavillon_id"]]
        au_dessus = [l for l in voisins if l["etage"] == parent["etage"] + 1] or voisins
        for categorie, sous_categorie, description, cible in [
            ("Plomberie", "Fuite de tuyau", "Recherche de la cause du dégât d'eau", rng.choice(au_dessus)),
            ("Électricité", "Disjoncteur déclenché", "Vérification électrique après dégât d'eau",
             next(l for l in locaux if l["local_id"] == parent["local_id"])),
        ]:
            if rng.random() < 0.6:
                creation = parent["_creation"] + timedelta(hours=rng.uniform(1, 30))
                if creation <= DATE_EXTRACTION:
                    suites.append(creer_bon(rng, creation, categorie, sous_categorie, description,
                                            cible, "Correctif", tech, parent=parent))
    return suites


def attribuer_identifiants(bons):
    bons.sort(key=lambda b: b["_creation"])
    for rang, bon in enumerate(bons, start=1):
        bon["bon_id"] = f"BT-{bon['_creation'].year}-{rang:06d}"
    for bon in bons:
        if bon["_parent"]:
            bon["bon_parent_id"] = bon["_parent"]["bon_id"]


def formater(valeur):
    return valeur.strftime("%Y-%m-%d %H:%M:%S") if isinstance(valeur, datetime) else valeur


def vers_lignes(bons):
    lignes = []
    for bon in bons:
        ligne = {c: formater(bon.get(c, "")) for c in COLONNES_BONS}
        ligne["date_creation"] = formater(bon["_creation"])
        lignes.append(ligne)
    return lignes


def injecter_defauts(rng, lignes):
    """Salit les données comme un vrai export ; chaque défaut est compté dans le manifeste."""
    fermes = [l for l in lignes if l["statut"] == "Fermé"]
    compte = {}

    def echantillon(population, taux):
        return rng.sample(population, int(len(population) * taux))

    choisis = echantillon(lignes, 0.06)
    for ligne in choisis:
        ligne["categorie"] = rng.choice(VARIANTES_CATEGORIE[ligne["categorie"]])
    compte["categories_mal_ecrites"] = len(choisis)

    choisis = echantillon(lignes, 0.015)
    for ligne in choisis:
        ligne["pavillon_id"] = rng.choice([ligne["pavillon_id"].lower(), ligne["pavillon_id"].replace("-", " "),
                                           f" {ligne['pavillon_id']}"])
    compte["pavillons_mal_ecrits"] = len(choisis)

    choisis = echantillon(fermes, 0.004)
    for ligne in choisis:
        creation = datetime.strptime(ligne["date_creation"], "%Y-%m-%d %H:%M:%S")
        ligne["date_fermeture"] = formater(creation - timedelta(hours=rng.uniform(1, 72)))
    compte["fermetures_avant_creation"] = len(choisis)

    choisis = echantillon([l for l in fermes if float(l["cout_pieces"]) > 0], 0.004)
    for ligne in choisis:
        ligne["cout_pieces"] = -ligne["cout_pieces"]
    compte["couts_negatifs"] = len(choisis)

    # Sans les coûts déjà négatifs, sinon ce défaut effacerait le précédent
    choisis = echantillon([l for l in fermes if l["cout_pieces"] >= 0], 0.02)
    for ligne in choisis:
        ligne["cout_pieces"] = ""
    compte["couts_pieces_manquants"] = len(choisis)

    choisis = echantillon(fermes, 0.015)
    for ligne in choisis:
        ligne["technicien_id"] = ""
    compte["techniciens_manquants"] = len(choisis)

    choisis = echantillon(fermes, 0.001)
    for ligne in choisis:
        ligne["heures_main_oeuvre"] = 999
    compte["heures_aberrantes"] = len(choisis)

    # Après les autres défauts, pour que les dates inversées puissent aussi changer de format
    choisis = echantillon(lignes, 0.05)
    for ligne in choisis:
        for champ in ("date_creation", "date_fermeture"):
            if ligne[champ]:
                valeur = datetime.strptime(ligne[champ], "%Y-%m-%d %H:%M:%S")
                ligne[champ] = valeur.strftime("%d/%m/%Y %H:%M")
    compte["dates_format_jj_mm_aaaa"] = len(choisis)

    # En dernier : un doublon est une copie exacte, défauts compris
    doublons = [dict(l) for l in echantillon(lignes, 0.008)]
    lignes.extend(doublons)
    rng.shuffle(lignes)
    compte["doublons_exacts"] = len(doublons)
    return compte


def ecrire_csv(chemin, colonnes, lignes):
    with open(chemin, "w", newline="", encoding="utf-8") as fichier:
        ecrivain = csv.DictWriter(fichier, fieldnames=colonnes, extrasaction="ignore")
        ecrivain.writeheader()
        ecrivain.writerows(lignes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graine", type=int, default=42)
    parser.add_argument("--sortie", type=Path, default=Path(__file__).resolve().parent.parent / "data" / "brut")
    args = parser.parse_args()

    rng = random.Random(args.graine)
    locaux = creer_locaux(rng)
    techniciens = creer_techniciens(rng)
    tech = {}
    for technicien in techniciens:
        tech.setdefault(technicien["metier"], []).append(technicien)

    bons = generer_courants(rng, locaux, tech)
    chroniques, verite = generer_points_chauds(rng, locaux, tech)
    bons += chroniques
    bons += generer_suites_degats(rng, bons, locaux, tech)
    attribuer_identifiants(bons)
    lignes = vers_lignes(bons)
    defauts = injecter_defauts(rng, lignes)

    args.sortie.mkdir(parents=True, exist_ok=True)
    ecrire_csv(args.sortie / "bons_de_travail.csv", COLONNES_BONS, lignes)
    ecrire_csv(args.sortie / "locaux.csv", ["local_id", "pavillon_id", "etage", "type_local", "criticite"], locaux)
    ecrire_csv(args.sortie / "techniciens.csv",
               ["technicien_id", "metier", "quart", "statut_emploi", "taux_horaire"], techniciens)
    colonnes_priorites = ["priorite", "libelle", "cible_fermeture_heures"]
    ecrire_csv(args.sortie / "priorites.csv", colonnes_priorites,
               [dict(zip(colonnes_priorites, p)) for p in PRIORITES])
    colonnes_pav = ["pavillon_id", "nom", "vocation", "annee_construction", "superficie_m2", "nb_etages"]
    ecrire_csv(args.sortie / "pavillons.csv", colonnes_pav, [dict(zip(colonnes_pav, p)) for p in PAVILLONS])

    manifeste = {
        "graine": args.graine, "periode": [DEBUT.isoformat(), FIN.isoformat()],
        "lignes_brutes": len(lignes), "bons_uniques": len(bons),
        "defauts_injectes": defauts, "points_chauds": verite,
    }
    (args.sortie.parent / "manifeste.json").write_text(
        json.dumps(manifeste, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(bons)} bons uniques, {len(lignes)} lignes brutes -> {args.sortie}")


if __name__ == "__main__":
    main()
