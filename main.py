# -*- coding: utf-8 -*-
import argparse
import os
import torch
import gc
import numpy as np
import pandas as pd
from torch.utils.data import DataLoader
import pickle
from sklearn.preprocessing import MinMaxScaler
import copy

from functions import select_criterion, cross_validation_process, delete_feature, join_cat_cont, predict_explain_models, export_explanation_to_excel, export_accuracy_to_excel
from datasets.loadData import kfold

parser = argparse.ArgumentParser()

# Parámetros obligatorios
parser.add_argument('--typeExecution', required=True, type=str, choices=['loadData', 'train', 'explain'])
parser.add_argument('--dset_id', required=True, type=int)
parser.add_argument('--task', required=True, type=str,choices = ['binary','multiclass','regression'])

# Parámetros de la ejecución
parser.add_argument('--epochs', default=100, type=int)
parser.add_argument('--batchsize', default=256, type=int)
parser.add_argument('--optimizer', default='AdamW', type=str,choices = ['AdamW','Adam','SGD'])
parser.add_argument('--scheduler', default='cosine', type=str,choices = ['cosine','linear'])
parser.add_argument('--lr', default=0.0001, type=float)
parser.add_argument('--savemodelroot', default=os.path.relpath('./models/trained'), type=str)
parser.add_argument('--savedatasetroot', default=os.path.relpath('./datasets/datasets_prepo'), type=str)
parser.add_argument('--saveresultroot', default=os.path.relpath('./results/'), type=str)
#parser.add_argument('--run_name', default='testrun', type=str)
parser.add_argument('--set_seed', default= 1 , type=int)

# Parámetros del Transformer
parser.add_argument('--transformer_depth', default=6, type=int)
parser.add_argument('--attention_heads', default=8, type=int)
parser.add_argument('--attention_dropout', default=0.1, type=float)
parser.add_argument('--attentiontype', default='colrow', type=str, choices = ['col','colrow','row','justmlp','attn','attnmlp']) #TODO: revisar
parser.add_argument('--ff_dropout', default=0.1, type=float)

# Parámetros del modelo SAINT
parser.add_argument('--embedding_size', default=32, type=int)
parser.add_argument('--cont_embeddings', default='MLP', type=str,choices = ['MLP','Noemb','pos_singleMLP'])
parser.add_argument('--final_mlp_style', default='common', type=str,choices = ['common','sep'])

# Otros parámetros
parser.add_argument('--dset_seed', default= 5 , type=int)

opt = parser.parse_args()

print("--------------------------------------------------------------------------")
print("\nIniciando ejecución...")

torch.manual_seed(opt.set_seed) 

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
print(f"\nDevice is {device}.\n")

#dir_datasets_path = "." + os.sep + opt.savedatasetroot + os.sep + opt.task + os.sep + str(opt.dset_id) #cargar datos normales
dir_datasets_path = "." + os.sep + opt.savedatasetroot + "_aleatorio" +  os.sep + opt.task + os.sep + str(opt.dset_id) #cargar datos + aleatorios
if opt.typeExecution == "loadData":
    if not os.path.exists(dir_datasets_path):
        os.makedirs(dir_datasets_path)
    
    if not os.path.exists(dir_datasets_path + os.sep + "train"):
        print("\nDescargando, procesando y guardando dataset con id " + str(opt.dset_id))
        folders = kfold(opt.dset_id, opt.dset_seed, opt.task, k=5)

        os.makedirs(dir_datasets_path + os.sep + "train" + os.sep)
        os.makedirs(dir_datasets_path + os.sep + "test" + os.sep)

        for _, key in enumerate(folders):
            for _, key2 in enumerate(folders[key]):
                dsdump = open(dir_datasets_path + os.sep + key2 + os.sep + "fold" + str(key) + ".pk", "wb")
                pickle.dump(folders[key][key2], dsdump)
                dsdump.close()
        del folders        
    else:
        print("\nERROR -  el dataset ya está descargado, procesado y guardado")

else:
    if not os.path.exists(dir_datasets_path):
        print("\nERROR - el dataset no está descargado, procesado y guardado")
    else:
        folders = {}
        for dir in os.scandir(dir_datasets_path):
            folders[dir.name] = {}
            if os.path.isdir(dir.path):
                for file in os.scandir(dir.path):
                    ds_file = open(file.path, "rb")
                    ds = pickle.load(ds_file)
                    folders[dir.name][file.name.split(".")[0]] = ds
                    ds_file.close()

    print("\nDatasets descargados, ajustando parámetros...")
    if opt.task == 'regression': #TODO: revisar, dtask creo que no se usa, ydim si
        opt.dtask = 'reg'
        y_dim = 1
    else:
        opt.dtask = 'clf'
        y_dim = folders["train"]["fold0"].num_classes

    cat_dims = [folders["train"]["fold0"].dataCat[i][2] for i in range(len(folders["train"]["fold0"].dataCat))] #list(zip(*(types_datasets["train"].dataCat)))[2] 
    cat_dims = np.append(np.array([1]),np.array(cat_dims)).astype(int) #Appending 1 for CLS token, this is later used to generate embeddings.
    nfeat_orig = folders["train"]["fold0"].cat.shape[1] + folders["train"]["fold0"].cont.shape[1] 
    limit = nfeat_orig - int(nfeat_orig * 0.75) #AVANZAR NO HASTA LA MITAD SINO HASTA EL 75%
    features_names = [data[1] for _, data in enumerate(folders["train"]["fold0"].dataCat)] + [data[1] for _, data in enumerate(folders["train"]["fold0"].dataCont)]
    
    if (nfeat_orig + 1) > 100:
        opt.embedding_size = min(8,opt.embedding_size)
        opt.batchsize = min(64, opt.batchsize)
    if opt.attentiontype != 'col':
        opt.transformer_depth = 1
        opt.attention_heads = min(4,opt.attention_heads)
        opt.attention_dropout = 0.8
        opt.embedding_size = min(32,opt.embedding_size)
        opt.ff_dropout = 0.8





    


    
    
    

    print("COMENZANDO VALIDACIÓN CRUZADA...\n")
    num_folders = len(folders["train"])
    criterion = select_criterion(y_dim, opt.task, device)
    dict_accuracy = {}
    dict_explanation = {}
    name_columns = ["particion"+str(k) for k in range(0, num_folders)]
    name_rows = ["nfeat"+str(nf) for nf in range(nfeat_orig, limit-1, -1)] # hasta el % 
    #name_rows = ["nfeat"+str(nf) for nf in range(nfeat_orig, 0, -1)] # solo nos quedamos con una variable
    print("----------------------------------------------Transformer: ")
    dataloader_folders = copy.deepcopy(folders)    
    accuracy = np.zeros(shape=(num_folders, nfeat_orig-limit+1)) # hasta el % 
    #accuracy = np.zeros(shape=(num_folders, nfeat_orig)) # solo nos quedamos con una variable
    explanation = np.zeros(shape=(num_folders, nfeat_orig-limit+1), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # hasta el %
    #explanation = np.zeros(shape=(num_folders, nfeat_orig), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # solo nos quedamos con una variable
    for k in range(0, num_folders):
        print("Partición " + str(k))
        trainloader = DataLoader(dataloader_folders["train"]["fold"+str(k)], batch_size=opt.batchsize, shuffle=True,num_workers=4) #print("\tNº datos en train: " + str(len(trainloader.dataset)))
        testloader = DataLoader(dataloader_folders["test"]["fold"+str(k)], batch_size=len(dataloader_folders["test"]["fold"+str(k)]), shuffle=False,num_workers=4) #print("\tNº datos en test: " + str(len(testloader.dataset)))print("\tNº datos en test: " + str(len(testloader.dataset)))
        nfeat = trainloader.dataset.cat.shape[1] + trainloader.dataset.cont.shape[1]  
        mms = MinMaxScaler()

        while nfeat >= limit: # hasta el %
        #while nfeat >= 1: # solo nos quedamos con una variable
            expls, metric_value = cross_validation_process(trainloader, testloader, y_dim, opt, device, criterion)
            accuracy[k][nfeat_orig-nfeat] = metric_value.item()
            attribute_names_ordered = [data[1] for _, data in enumerate(trainloader.dataset.dataCat)] + [data[1] for _, data in enumerate(trainloader.dataset.dataCont)]
            mean_feature_relevance = mms.fit_transform(expls.mean(dim=0).cpu().detach().numpy().reshape(-1, 1)).reshape(1, -1)       
            expl_for_save = dict(zip(attribute_names_ordered, mean_feature_relevance[0]))
            for key in expl_for_save:
                explanation[k][nfeat_orig-nfeat][key] = np.float64(expl_for_save[key])
            feature_deleted = np.argmin(mean_feature_relevance)            
            print("\t\tNombre variables: " + str(attribute_names_ordered))
            print("\t\tRelevancia de las variables: " + str(mean_feature_relevance))
            print("\t\tEliminamos una variable... con id: " + str(feature_deleted))
            trainloader, testloader, nfeat = delete_feature(trainloader, testloader, feature_deleted)

        torch.cuda.empty_cache()
        gc.collect()

    dict_accuracy["Transformer"] = pd.DataFrame(accuracy.transpose(), columns=name_columns, index=name_rows) 
    dfs_explanation = []
    for feature in features_names:
        dfs_explanation.append((feature, pd.DataFrame(explanation[feature].transpose(), columns=name_columns, index=name_rows)))
    dict_explanation["Transformer"] = dfs_explanation





    print("----------------------------------------------Transformer INVERSE: ")
    dataloader_folders = copy.deepcopy(folders)    
    accuracy = np.zeros(shape=(num_folders, nfeat_orig-limit+1)) # hasta el % 
    #accuracy = np.zeros(shape=(num_folders, nfeat_orig)) # solo nos quedamos con una variable
    explanation = np.zeros(shape=(num_folders, nfeat_orig-limit+1), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # hasta el %
    #explanation = np.zeros(shape=(num_folders, nfeat_orig), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # solo nos quedamos con una variable
    for k in range(0, num_folders):
        print("Partición " + str(k))
        trainloader = DataLoader(dataloader_folders["train"]["fold"+str(k)], batch_size=opt.batchsize, shuffle=True,num_workers=4) #print("\tNº datos en train: " + str(len(trainloader.dataset)))
        testloader = DataLoader(dataloader_folders["test"]["fold"+str(k)], batch_size=len(dataloader_folders["test"]["fold"+str(k)]), shuffle=False,num_workers=4) #print("\tNº datos en test: " + str(len(testloader.dataset)))print("\tNº datos en test: " + str(len(testloader.dataset)))
        nfeat = trainloader.dataset.cat.shape[1] + trainloader.dataset.cont.shape[1]  
        mms = MinMaxScaler()

        while nfeat >= limit: # hasta el %
        #while nfeat >= 1: # solo nos quedamos con una variable
            expls, metric_value = cross_validation_process(trainloader, testloader, y_dim, opt, device, criterion)
            accuracy[k][nfeat_orig-nfeat] = metric_value.item()
            attribute_names_ordered = [data[1] for _, data in enumerate(trainloader.dataset.dataCat)] + [data[1] for _, data in enumerate(trainloader.dataset.dataCont)]
            mean_feature_relevance = mms.fit_transform(expls.mean(dim=0).cpu().detach().numpy().reshape(-1, 1)).reshape(1, -1)       
            expl_for_save = dict(zip(attribute_names_ordered, mean_feature_relevance[0]))
            for key in expl_for_save:
                explanation[k][nfeat_orig-nfeat][key] = np.float64(expl_for_save[key])
            feature_deleted = np.argmax(mean_feature_relevance)            
            print("\t\tNombre variables: " + str(attribute_names_ordered))
            print("\t\tRelevancia de las variables: " + str(mean_feature_relevance))
            print("\t\tEliminamos una variable... con id: " + str(feature_deleted))
            trainloader, testloader, nfeat = delete_feature(trainloader, testloader, feature_deleted)

        torch.cuda.empty_cache()
        gc.collect()

    dict_accuracy["TransformerINVERSE"] = pd.DataFrame(accuracy.transpose(), columns=name_columns, index=name_rows) 
    dfs_explanation = []
    for feature in features_names:
        dfs_explanation.append((feature, pd.DataFrame(explanation[feature].transpose(), columns=name_columns, index=name_rows)))
    dict_explanation["TransformerINVERSE"] = dfs_explanation












    from sklearn.svm import SVC
    svc = SVC(kernel="linear", probability=True)
    print("\n\n---------------------------------------------SVM: ")
    dataloader_folders = copy.deepcopy(folders)
    accuracy = np.zeros(shape=(num_folders, nfeat_orig-limit+1)) # hasta el % 
    #accuracy = np.zeros(shape=(num_folders, nfeat_orig)) # solo nos quedamos con una variable
    explanation = np.zeros(shape=(num_folders, nfeat_orig-limit+1), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # hasta el %
    #explanation = np.zeros(shape=(num_folders, nfeat_orig), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # solo nos quedamos con una variable
    for k in range(0, num_folders):
        print("Partición " + str(k))
        trainloader = DataLoader(dataloader_folders["train"]["fold"+str(k)], batch_size=opt.batchsize, shuffle=True,num_workers=4) #print("\tNº datos en train: " + str(len(trainloader.dataset)))
        testloader = DataLoader(dataloader_folders["test"]["fold"+str(k)], batch_size=len(dataloader_folders["test"]["fold"+str(k)]), shuffle=False,num_workers=4) #print("\tNº datos en test: " + str(len(testloader.dataset)))print("\tNº datos en test: " + str(len(testloader.dataset)))
        nfeat = trainloader.dataset.cat.shape[1] + trainloader.dataset.cont.shape[1]
        mms = MinMaxScaler()

        while nfeat >= limit: # hasta el %
        #while nfeat >= 1: # solo nos quedamos con una variable
            X_train, y_train, X_test, y_test = join_cat_cont(trainloader, testloader)
            svc.fit(X_train, y_train.ravel())
            expls, metric_value = predict_explain_models(svc, X_train, X_test, y_test, device, False)
            accuracy[k][nfeat_orig-nfeat] = metric_value.item()
            attribute_names_ordered = [data[1] for _, data in enumerate(trainloader.dataset.dataCat)] + [data[1] for _, data in enumerate(trainloader.dataset.dataCont)]
            mean_feature_relevance = mms.fit_transform(expls.mean(dim=0).cpu().detach().numpy().reshape(-1, 1)).reshape(1, -1)
            expl_for_save = dict(zip(attribute_names_ordered, mean_feature_relevance[0]))
            for key in expl_for_save:
                explanation[k][nfeat_orig-nfeat][key] = np.float64(expl_for_save[key])
            feature_deleted = np.argmin(mean_feature_relevance)                  
            print("\t\tNombre variables: " + str(attribute_names_ordered))
            print("\t\tRelevancia de las variables: " + str(mean_feature_relevance))
            print("\t\tEliminamos una variable... con id: " + str(feature_deleted))
            trainloader, testloader, nfeat = delete_feature(trainloader, testloader, feature_deleted)
            
        torch.cuda.empty_cache()
        gc.collect()

    dict_accuracy["SVM"] = pd.DataFrame(accuracy.transpose(), columns=name_columns, index=name_rows) 
    dfs_explanation = []
    for feature in features_names:
        dfs_explanation.append((feature, pd.DataFrame(explanation[feature].transpose(), columns=name_columns, index=name_rows)))
    dict_explanation["SVM"] = dfs_explanation





    print("\n\n---------------------------------------------SVM INVERSE: ")
    dataloader_folders = copy.deepcopy(folders)
    accuracy = np.zeros(shape=(num_folders, nfeat_orig-limit+1)) # hasta el % 
    #accuracy = np.zeros(shape=(num_folders, nfeat_orig)) # solo nos quedamos con una variable
    explanation = np.zeros(shape=(num_folders, nfeat_orig-limit+1), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # hasta el %
    #explanation = np.zeros(shape=(num_folders, nfeat_orig), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # solo nos quedamos con una variable
    for k in range(0, num_folders):
        print("Partición " + str(k))
        trainloader = DataLoader(dataloader_folders["train"]["fold"+str(k)], batch_size=opt.batchsize, shuffle=True,num_workers=4) #print("\tNº datos en train: " + str(len(trainloader.dataset)))
        testloader = DataLoader(dataloader_folders["test"]["fold"+str(k)], batch_size=len(dataloader_folders["test"]["fold"+str(k)]), shuffle=False,num_workers=4) #print("\tNº datos en test: " + str(len(testloader.dataset)))print("\tNº datos en test: " + str(len(testloader.dataset)))
        nfeat = trainloader.dataset.cat.shape[1] + trainloader.dataset.cont.shape[1]
        mms = MinMaxScaler()

        while nfeat >= limit: # hasta el %
        #while nfeat >= 1: # solo nos quedamos con una variable
            X_train, y_train, X_test, y_test = join_cat_cont(trainloader, testloader)
            svc.fit(X_train, y_train.ravel())
            expls, metric_value = predict_explain_models(svc, X_train, X_test, y_test, device, False)
            accuracy[k][nfeat_orig-nfeat] = metric_value.item()
            attribute_names_ordered = [data[1] for _, data in enumerate(trainloader.dataset.dataCat)] + [data[1] for _, data in enumerate(trainloader.dataset.dataCont)]
            mean_feature_relevance = mms.fit_transform(expls.mean(dim=0).cpu().detach().numpy().reshape(-1, 1)).reshape(1, -1)
            expl_for_save = dict(zip(attribute_names_ordered, mean_feature_relevance[0]))
            for key in expl_for_save:
                explanation[k][nfeat_orig-nfeat][key] = np.float64(expl_for_save[key])
            feature_deleted = np.argmax(mean_feature_relevance)                  
            print("\t\tNombre variables: " + str(attribute_names_ordered))
            print("\t\tRelevancia de las variables: " + str(mean_feature_relevance))
            print("\t\tEliminamos una variable... con id: " + str(feature_deleted))
            trainloader, testloader, nfeat = delete_feature(trainloader, testloader, feature_deleted)
            
        torch.cuda.empty_cache()
        gc.collect()

    dict_accuracy["SVM_INVERSE"] = pd.DataFrame(accuracy.transpose(), columns=name_columns, index=name_rows) 
    dfs_explanation = []
    for feature in features_names:
        dfs_explanation.append((feature, pd.DataFrame(explanation[feature].transpose(), columns=name_columns, index=name_rows)))
    dict_explanation["SVM_INVERSE"] = dfs_explanation








    from sklearn.neighbors import KNeighborsClassifier
    knn = KNeighborsClassifier(n_neighbors=3)
    print("\n\n---------------------------------------------KNN: ")
    dataloader_folders = copy.deepcopy(folders)
    accuracy = np.zeros(shape=(num_folders, nfeat_orig-limit+1)) # hasta el % 
    #accuracy = np.zeros(shape=(num_folders, nfeat_orig)) # solo nos quedamos con una variable
    explanation = np.zeros(shape=(num_folders, nfeat_orig-limit+1), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # hasta el %
    #explanation = np.zeros(shape=(num_folders, nfeat_orig), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # solo nos quedamos con una variable
    for k in range(0, num_folders):
        print("Partición " + str(k))
        trainloader = DataLoader(dataloader_folders["train"]["fold"+str(k)], batch_size=opt.batchsize, shuffle=True,num_workers=4) #print("\tNº datos en train: " + str(len(trainloader.dataset)))
        testloader = DataLoader(dataloader_folders["test"]["fold"+str(k)], batch_size=len(dataloader_folders["test"]["fold"+str(k)]), shuffle=False,num_workers=4) #print("\tNº datos en test: " + str(len(testloader.dataset)))print("\tNº datos en test: " + str(len(testloader.dataset)))
        nfeat = trainloader.dataset.cat.shape[1] + trainloader.dataset.cont.shape[1]
        mms = MinMaxScaler()

        while nfeat >= limit: # hasta el %
        #while nfeat >= 1: # solo nos quedamos con una variable
            X_train, y_train, X_test, y_test = join_cat_cont(trainloader, testloader)
            knn.fit(X_train, y_train.ravel())
            expls, metric_value = predict_explain_models(knn, X_train, X_test, y_test, device, False)
            accuracy[k][nfeat_orig-nfeat] = metric_value.item()
            attribute_names_ordered = [data[1] for _, data in enumerate(trainloader.dataset.dataCat)] + [data[1] for _, data in enumerate(trainloader.dataset.dataCont)]
            mean_feature_relevance = mms.fit_transform(expls.mean(dim=0).cpu().detach().numpy().reshape(-1, 1)).reshape(1, -1)
            expl_for_save = dict(zip(attribute_names_ordered, mean_feature_relevance[0]))
            for key in expl_for_save:
                explanation[k][nfeat_orig-nfeat][key] = np.float64(expl_for_save[key])
            feature_deleted = np.argmin(mean_feature_relevance)  
            print("\t\tNombre variables: " + str(attribute_names_ordered))
            print("\t\tRelevancia de las variables: " + str(mean_feature_relevance))
            print("\t\tEliminamos una variable... con id: " + str(feature_deleted))
            trainloader, testloader, nfeat = delete_feature(trainloader, testloader, feature_deleted)

        torch.cuda.empty_cache()
        gc.collect()

    dict_accuracy["KNN"] = pd.DataFrame(accuracy.transpose(), columns=name_columns, index=name_rows) 
    dfs_explanation = []
    for feature in features_names:
        dfs_explanation.append((feature, pd.DataFrame(explanation[feature].transpose(), columns=name_columns, index=name_rows)))
    dict_explanation["KNN"] = dfs_explanation





    print("\n\n---------------------------------------------KNN INVERSE: ")
    dataloader_folders = copy.deepcopy(folders)
    accuracy = np.zeros(shape=(num_folders, nfeat_orig-limit+1)) # hasta el % 
    #accuracy = np.zeros(shape=(num_folders, nfeat_orig)) # solo nos quedamos con una variable
    explanation = np.zeros(shape=(num_folders, nfeat_orig-limit+1), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # hasta el %
    #explanation = np.zeros(shape=(num_folders, nfeat_orig), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # solo nos quedamos con una variable
    for k in range(0, num_folders):
        print("Partición " + str(k))
        trainloader = DataLoader(dataloader_folders["train"]["fold"+str(k)], batch_size=opt.batchsize, shuffle=True,num_workers=4) #print("\tNº datos en train: " + str(len(trainloader.dataset)))
        testloader = DataLoader(dataloader_folders["test"]["fold"+str(k)], batch_size=len(dataloader_folders["test"]["fold"+str(k)]), shuffle=False,num_workers=4) #print("\tNº datos en test: " + str(len(testloader.dataset)))print("\tNº datos en test: " + str(len(testloader.dataset)))
        nfeat = trainloader.dataset.cat.shape[1] + trainloader.dataset.cont.shape[1]
        mms = MinMaxScaler()

        while nfeat >= limit: # hasta el %
        #while nfeat >= 1: # solo nos quedamos con una variable
            X_train, y_train, X_test, y_test = join_cat_cont(trainloader, testloader)
            knn.fit(X_train, y_train.ravel())
            expls, metric_value = predict_explain_models(knn, X_train, X_test, y_test, device, False)
            accuracy[k][nfeat_orig-nfeat] = metric_value.item()
            attribute_names_ordered = [data[1] for _, data in enumerate(trainloader.dataset.dataCat)] + [data[1] for _, data in enumerate(trainloader.dataset.dataCont)]
            mean_feature_relevance = mms.fit_transform(expls.mean(dim=0).cpu().detach().numpy().reshape(-1, 1)).reshape(1, -1)
            expl_for_save = dict(zip(attribute_names_ordered, mean_feature_relevance[0]))
            for key in expl_for_save:
                explanation[k][nfeat_orig-nfeat][key] = np.float64(expl_for_save[key])
            feature_deleted = np.argmax(mean_feature_relevance)  
            print("\t\tNombre variables: " + str(attribute_names_ordered))
            print("\t\tRelevancia de las variables: " + str(mean_feature_relevance))
            print("\t\tEliminamos una variable... con id: " + str(feature_deleted))
            trainloader, testloader, nfeat = delete_feature(trainloader, testloader, feature_deleted)

        torch.cuda.empty_cache()
        gc.collect()

    dict_accuracy["KNN_INVERSE"] = pd.DataFrame(accuracy.transpose(), columns=name_columns, index=name_rows) 
    dfs_explanation = []
    for feature in features_names:
        dfs_explanation.append((feature, pd.DataFrame(explanation[feature].transpose(), columns=name_columns, index=name_rows)))
    dict_explanation["KNN_INVERSE"] = dfs_explanation





    





    from sklearn.neural_network import MLPClassifier
    mlp = MLPClassifier()
    print("\n\n---------------------------------------------MLP: ")
    dataloader_folders = copy.deepcopy(folders)
    accuracy = np.zeros(shape=(num_folders, nfeat_orig-limit+1)) # hasta el % 
    #accuracy = np.zeros(shape=(num_folders, nfeat_orig)) # solo nos quedamos con una variable
    explanation = np.zeros(shape=(num_folders, nfeat_orig-limit+1), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # hasta el %
    #explanation = np.zeros(shape=(num_folders, nfeat_orig), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # solo nos quedamos con una variable
    for k in range(0, num_folders):
        print("Partición " + str(k))
        trainloader = DataLoader(dataloader_folders["train"]["fold"+str(k)], batch_size=opt.batchsize, shuffle=True,num_workers=4) #print("\tNº datos en train: " + str(len(trainloader.dataset)))
        testloader = DataLoader(dataloader_folders["test"]["fold"+str(k)], batch_size=len(dataloader_folders["test"]["fold"+str(k)]), shuffle=False,num_workers=4) #print("\tNº datos en test: " + str(len(testloader.dataset)))print("\tNº datos en test: " + str(len(testloader.dataset)))
        nfeat = trainloader.dataset.cat.shape[1] + trainloader.dataset.cont.shape[1]
        mms = MinMaxScaler()

        while nfeat >= limit: # hasta el %
        #while nfeat >= 1: # solo nos quedamos con una variable
            X_train, y_train, X_test, y_test = join_cat_cont(trainloader, testloader)
            mlp.fit(X_train, y_train.ravel())
            expls, metric_value = predict_explain_models(mlp, X_train, X_test, y_test, device, False)
            accuracy[k][nfeat_orig-nfeat] = metric_value.item()
            attribute_names_ordered = [data[1] for _, data in enumerate(trainloader.dataset.dataCat)] + [data[1] for _, data in enumerate(trainloader.dataset.dataCont)]
            mean_feature_relevance = mms.fit_transform(expls.mean(dim=0).cpu().detach().numpy().reshape(-1, 1)).reshape(1, -1)
            expl_for_save = dict(zip(attribute_names_ordered, mean_feature_relevance[0]))
            for key in expl_for_save:
                explanation[k][nfeat_orig-nfeat][key] = np.float64(expl_for_save[key])
            feature_deleted = np.argmin(mean_feature_relevance)  
            print("\t\tNombre variables: " + str(attribute_names_ordered))
            print("\t\tRelevancia de las variables: " + str(mean_feature_relevance))
            print("\t\tEliminamos una variable... con id: " + str(feature_deleted))
            trainloader, testloader, nfeat = delete_feature(trainloader, testloader, feature_deleted)

        torch.cuda.empty_cache()
        gc.collect()

    dict_accuracy["MLP"] = pd.DataFrame(accuracy.transpose(), columns=name_columns, index=name_rows) 
    dfs_explanation = []
    for feature in features_names:
        dfs_explanation.append((feature, pd.DataFrame(explanation[feature].transpose(), columns=name_columns, index=name_rows)))
    dict_explanation["MLP"] = dfs_explanation







    print("\n\n---------------------------------------------MLP INVERSE: ")
    dataloader_folders = copy.deepcopy(folders)
    accuracy = np.zeros(shape=(num_folders, nfeat_orig-limit+1)) # hasta el % 
    #accuracy = np.zeros(shape=(num_folders, nfeat_orig)) # solo nos quedamos con una variable
    explanation = np.zeros(shape=(num_folders, nfeat_orig-limit+1), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # hasta el %
    #explanation = np.zeros(shape=(num_folders, nfeat_orig), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # solo nos quedamos con una variable
    for k in range(0, num_folders):
        print("Partición " + str(k))
        trainloader = DataLoader(dataloader_folders["train"]["fold"+str(k)], batch_size=opt.batchsize, shuffle=True,num_workers=4) #print("\tNº datos en train: " + str(len(trainloader.dataset)))
        testloader = DataLoader(dataloader_folders["test"]["fold"+str(k)], batch_size=len(dataloader_folders["test"]["fold"+str(k)]), shuffle=False,num_workers=4) #print("\tNº datos en test: " + str(len(testloader.dataset)))print("\tNº datos en test: " + str(len(testloader.dataset)))
        nfeat = trainloader.dataset.cat.shape[1] + trainloader.dataset.cont.shape[1]
        mms = MinMaxScaler()

        while nfeat >= limit: # hasta el %
        #while nfeat >= 1: # solo nos quedamos con una variable
            X_train, y_train, X_test, y_test = join_cat_cont(trainloader, testloader)
            mlp.fit(X_train, y_train.ravel())
            expls, metric_value = predict_explain_models(mlp, X_train, X_test, y_test, device, False)
            accuracy[k][nfeat_orig-nfeat] = metric_value.item()
            attribute_names_ordered = [data[1] for _, data in enumerate(trainloader.dataset.dataCat)] + [data[1] for _, data in enumerate(trainloader.dataset.dataCont)]
            mean_feature_relevance = mms.fit_transform(expls.mean(dim=0).cpu().detach().numpy().reshape(-1, 1)).reshape(1, -1)
            expl_for_save = dict(zip(attribute_names_ordered, mean_feature_relevance[0]))
            for key in expl_for_save:
                explanation[k][nfeat_orig-nfeat][key] = np.float64(expl_for_save[key])
            feature_deleted = np.argmax(mean_feature_relevance)  
            print("\t\tNombre variables: " + str(attribute_names_ordered))
            print("\t\tRelevancia de las variables: " + str(mean_feature_relevance))
            print("\t\tEliminamos una variable... con id: " + str(feature_deleted))
            trainloader, testloader, nfeat = delete_feature(trainloader, testloader, feature_deleted)

        torch.cuda.empty_cache()
        gc.collect()

    dict_accuracy["MLP_INVERSE"] = pd.DataFrame(accuracy.transpose(), columns=name_columns, index=name_rows) 
    dfs_explanation = []
    for feature in features_names:
        dfs_explanation.append((feature, pd.DataFrame(explanation[feature].transpose(), columns=name_columns, index=name_rows)))
    dict_explanation["MLP_INVERSE"] = dfs_explanation







    from sklearn.ensemble import RandomForestClassifier
    rf = RandomForestClassifier()
    print("\n\n---------------------------------------------Random Forest: ")
    dataloader_folders = copy.deepcopy(folders)
    accuracy = np.zeros(shape=(num_folders, nfeat_orig-limit+1)) # hasta el % 
    #accuracy = np.zeros(shape=(num_folders, nfeat_orig)) # solo nos quedamos con una variable
    explanation = np.zeros(shape=(num_folders, nfeat_orig-limit+1), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # hasta el %
    #explanation = np.zeros(shape=(num_folders, nfeat_orig), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # solo nos quedamos con una variable
    for k in range(0, num_folders):        
        print("Partición " + str(k))
        trainloader = DataLoader(dataloader_folders["train"]["fold"+str(k)], batch_size=opt.batchsize, shuffle=True,num_workers=4) #print("\tNº datos en train: " + str(len(trainloader.dataset)))
        testloader = DataLoader(dataloader_folders["test"]["fold"+str(k)], batch_size=len(dataloader_folders["test"]["fold"+str(k)]), shuffle=False,num_workers=4) #print("\tNº datos en test: " + str(len(testloader.dataset)))print("\tNº datos en test: " + str(len(testloader.dataset)))
        nfeat = trainloader.dataset.cat.shape[1] + trainloader.dataset.cont.shape[1]
        mms = MinMaxScaler()

        while nfeat >= limit: # hasta el %
        #while nfeat >= 1: # solo nos quedamos con una variable
            X_train, y_train, X_test, y_test = join_cat_cont(trainloader, testloader)
            mlp.fit(X_train, y_train.ravel())
            expls, metric_value = predict_explain_models(mlp, X_train, X_test, y_test, device, False)
            accuracy[k][nfeat_orig-nfeat] = metric_value.item()
            attribute_names_ordered = [data[1] for _, data in enumerate(trainloader.dataset.dataCat)] + [data[1] for _, data in enumerate(trainloader.dataset.dataCont)]
            mean_feature_relevance = mms.fit_transform(expls.mean(dim=0).cpu().detach().numpy().reshape(-1, 1)).reshape(1, -1)
            expl_for_save = dict(zip(attribute_names_ordered, mean_feature_relevance[0]))
            for key in expl_for_save:
                explanation[k][nfeat_orig-nfeat][key] = np.float64(expl_for_save[key])
            feature_deleted = np.argmin(mean_feature_relevance)  
            print("\t\tNombre variables: " + str(attribute_names_ordered))
            print("\t\tRelevancia de las variables: " + str(mean_feature_relevance))
            print("\t\tEliminamos una variable... con id: " + str(feature_deleted))
            trainloader, testloader, nfeat = delete_feature(trainloader, testloader, feature_deleted)

        torch.cuda.empty_cache()
        gc.collect()

    dict_accuracy["RandomForest"] = pd.DataFrame(accuracy.transpose(), columns=name_columns, index=name_rows) 
    dfs_explanation = []
    for feature in features_names:
        dfs_explanation.append((feature, pd.DataFrame(explanation[feature].transpose(), columns=name_columns, index=name_rows)))
    dict_explanation["RandomForest"] = dfs_explanation






    print("\n\n---------------------------------------------Random Forest INVERSE: ")
    dataloader_folders = copy.deepcopy(folders)
    accuracy = np.zeros(shape=(num_folders, nfeat_orig-limit+1)) # hasta el % 
    #accuracy = np.zeros(shape=(num_folders, nfeat_orig)) # solo nos quedamos con una variable
    explanation = np.zeros(shape=(num_folders, nfeat_orig-limit+1), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # hasta el %
    #explanation = np.zeros(shape=(num_folders, nfeat_orig), dtype=list(zip(features_names, [np.float64 for i in range(0, len(features_names))]))) # solo nos quedamos con una variable
    for k in range(0, num_folders):        
        print("Partición " + str(k))
        trainloader = DataLoader(dataloader_folders["train"]["fold"+str(k)], batch_size=opt.batchsize, shuffle=True,num_workers=4) #print("\tNº datos en train: " + str(len(trainloader.dataset)))
        testloader = DataLoader(dataloader_folders["test"]["fold"+str(k)], batch_size=len(dataloader_folders["test"]["fold"+str(k)]), shuffle=False,num_workers=4) #print("\tNº datos en test: " + str(len(testloader.dataset)))print("\tNº datos en test: " + str(len(testloader.dataset)))
        nfeat = trainloader.dataset.cat.shape[1] + trainloader.dataset.cont.shape[1]
        mms = MinMaxScaler()

        while nfeat >= limit: # hasta el %
        #while nfeat >= 1: # solo nos quedamos con una variable
            X_train, y_train, X_test, y_test = join_cat_cont(trainloader, testloader)
            mlp.fit(X_train, y_train.ravel())
            expls, metric_value = predict_explain_models(mlp, X_train, X_test, y_test, device, False)
            accuracy[k][nfeat_orig-nfeat] = metric_value.item()
            attribute_names_ordered = [data[1] for _, data in enumerate(trainloader.dataset.dataCat)] + [data[1] for _, data in enumerate(trainloader.dataset.dataCont)]
            mean_feature_relevance = mms.fit_transform(expls.mean(dim=0).cpu().detach().numpy().reshape(-1, 1)).reshape(1, -1)
            expl_for_save = dict(zip(attribute_names_ordered, mean_feature_relevance[0]))
            for key in expl_for_save:
                explanation[k][nfeat_orig-nfeat][key] = np.float64(expl_for_save[key])
            feature_deleted = np.argmax(mean_feature_relevance)  
            print("\t\tNombre variables: " + str(attribute_names_ordered))
            print("\t\tRelevancia de las variables: " + str(mean_feature_relevance))
            print("\t\tEliminamos una variable... con id: " + str(feature_deleted))
            trainloader, testloader, nfeat = delete_feature(trainloader, testloader, feature_deleted)

        torch.cuda.empty_cache()
        gc.collect()

    dict_accuracy["RandomForestINVERSE"] = pd.DataFrame(accuracy.transpose(), columns=name_columns, index=name_rows) 
    dfs_explanation = []
    for feature in features_names:
        dfs_explanation.append((feature, pd.DataFrame(explanation[feature].transpose(), columns=name_columns, index=name_rows)))
    dict_explanation["RandomForestINVERSE"] = dfs_explanation








    result_path = "." + os.sep + opt.saveresultroot
    if not os.path.exists(result_path):
        os.makedirs(result_path)
    export_accuracy_to_excel(result_path, trainloader.dataset.name, dict_accuracy, nfeat_orig-limit+1, name_columns, name_rows) # hasta el %
    #export_accuracy_to_excel(result_path, trainloader.dataset.name, dict_accuracy, nfeat_orig, name_columns, name_rows) # solo nos quedamos con una variable
    export_explanation_to_excel(result_path, trainloader.dataset.name, dict_explanation)

    print("END")

torch.cuda.empty_cache()
gc.collect()